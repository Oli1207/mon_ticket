import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from events.models import Event

from .exceptions import OrderError, SoldOut, TooManyPendingOrders
from .models import Order, OrderItem, PricingSettings, PromoCode, Ticket, TicketType
from .pricing import compute_order_amounts, resolve_commission_percent

logger = logging.getLogger(__name__)

EMAIL_LOCK_TTL_SECONDS = 15


def stats_cache_key(event_id):
    return f'event_stats:v1:{event_id}'


def event_detail_cache_key(slug):
    return f'event_detail:v1:{slug}'


def invalidate_event_caches(event):
    cache.delete_many([stats_cache_key(event.pk), event_detail_cache_key(event.slug)])


def _check_anti_hoarding(pricing, email, client_ip, now):
    pending = Order.objects.filter(status=Order.STATUS_PENDING, expires_at__gt=now)
    if pending.filter(email=email).count() >= pricing.max_pending_per_email:
        raise TooManyPendingOrders(
            'Tu as deja des commandes en attente de paiement. Finalise-les ou attends leur expiration '
            f'({pricing.reservation_minutes} minutes max) avant d\'en creer une nouvelle.'
        )
    if pending.filter(client_ip=client_ip).count() >= pricing.max_pending_per_ip:
        raise TooManyPendingOrders(
            'Trop de commandes en attente depuis ce reseau. Reessaie dans quelques minutes.'
        )


def _load_promo(event, code, now):
    if not code:
        return None
    promo = PromoCode.objects.filter(event=event, code=code.upper()).first()
    if (
        promo is None
        or not promo.is_active
        or (promo.expires_at is not None and promo.expires_at <= now)
        or (promo.max_uses is not None and promo.uses >= promo.max_uses)
    ):
        raise OrderError({'promo_code': ['Ce code promo est invalide ou expire.']})
    return promo


def create_order(*, event_slug, items, email, phone, full_name, client_ip, user=None, promo_code=''):
    """
    Reserve les billets et cree une commande en attente de paiement.

    Les TicketType sont verrouilles (select_for_update, ordre de pk stable pour eviter les
    interblocages) le temps d'une transaction tres courte : verification du stock + increment
    de reserved_count. La contrainte CHECK en base est le dernier rempart contre la survente.
    """
    now = timezone.now()
    pricing = PricingSettings.load()

    event = (
        Event.objects.public()
        .select_related('organization', 'ticketing', 'ticketing__partner_code')
        .filter(slug=event_slug)
        .first()
    )
    if event is None:
        raise OrderError({'event': ['Evenement introuvable.']})
    ticketing = getattr(event, 'ticketing', None)
    if ticketing is None or not ticketing.sales_open:
        raise OrderError('La billetterie de cet evenement est fermee.')
    if (event.end_at or event.start_at) <= now:
        raise OrderError('Cet evenement est termine.')

    total_quantity = sum(item['quantity'] for item in items)
    if total_quantity < 1:
        raise OrderError('Choisis au moins un billet.')
    if total_quantity > pricing.max_tickets_per_order:
        raise OrderError(f'Tu peux acheter au maximum {pricing.max_tickets_per_order} billets par commande.')

    # Verrou court par email : empeche de contourner max_pending_per_email par des requetes paralleles.
    lock_key = f'order_create_lock:{email}'
    if not cache.add(lock_key, 1, EMAIL_LOCK_TTL_SECONDS):
        raise TooManyPendingOrders('Une commande est deja en cours de creation pour cet email. Patiente quelques secondes.')
    try:
        _check_anti_hoarding(pricing, email, client_ip, now)
        promo = _load_promo(event, promo_code, now)
        quantities = {item['ticket_type_id']: item['quantity'] for item in items}

        with transaction.atomic():
            ticket_types = list(
                TicketType.objects.select_for_update()
                .filter(pk__in=quantities.keys(), event=event)
                .order_by('pk')
            )
            if len(ticket_types) != len(quantities):
                raise OrderError({'items': ['Un des billets demandes est introuvable.']})

            lines = []
            for tt in ticket_types:
                qty = quantities[tt.pk]
                if not tt.is_on_sale(now):
                    raise OrderError({'items': [f'Le billet « {tt.name} » n\'est pas en vente actuellement.']})
                if qty > tt.max_per_order:
                    raise OrderError({'items': [f'Maximum {tt.max_per_order} billets « {tt.name} » par commande.']})
                if qty > tt.available:
                    if tt.available == 0:
                        raise SoldOut(f'Le billet « {tt.name} » est epuise.')
                    raise SoldOut(f'Il ne reste que {tt.available} billet(s) « {tt.name} ».')
                pct = resolve_commission_percent(pricing, ticketing, tt, now)
                lines.append((tt, qty, pct))

            amounts = compute_order_amounts(
                [(tt.price, qty, pct) for tt, qty, pct in lines], ticketing.fees_mode, promo
            )
            if amounts['total'] > 0 and not event.organization.paystack_subaccount_code:
                raise OrderError("Le paiement n'est pas encore active pour cet organisateur.")

            if promo is not None:
                consumed = PromoCode.objects.filter(
                    Q(max_uses__isnull=True) | Q(uses__lt=F('max_uses')), pk=promo.pk
                ).update(uses=F('uses') + 1)
                if not consumed:
                    raise OrderError({'promo_code': ['Ce code promo a atteint sa limite d\'utilisation.']})

            order = Order.objects.create(
                event=event,
                user=user if user is not None and user.is_authenticated else None,
                email=email,
                phone=phone,
                full_name=full_name,
                client_ip=client_ip,
                promo_code=promo,
                fees_mode=ticketing.fees_mode,
                expires_at=now + timedelta(minutes=pricing.reservation_minutes),
                **amounts,
            )
            OrderItem.objects.bulk_create([
                OrderItem(order=order, ticket_type=tt, quantity=qty, unit_price=tt.price, commission_percent=pct)
                for tt, qty, pct in lines
            ])
            for tt, qty, _ in lines:
                TicketType.objects.filter(pk=tt.pk).update(reserved_count=F('reserved_count') + qty)

            if order.total == 0:
                _mark_paid(order, reference='', paid_at=now, from_reservation=True)
    finally:
        cache.delete(lock_key)

    invalidate_event_caches(event)
    return order


def _issue_tickets(order):
    items = list(order.items.select_related('ticket_type').order_by('ticket_type_id'))
    Ticket.objects.bulk_create([
        Ticket(
            order=order,
            event_id=order.event_id,
            ticket_type=item.ticket_type,
            holder_name=order.full_name,
            holder_email=order.email,
        )
        for item in items
        for _ in range(item.quantity)
    ])


def _mark_paid(order, reference, paid_at, from_reservation):
    items = list(order.items.order_by('ticket_type_id'))
    if from_reservation:
        for item in items:
            TicketType.objects.filter(pk=item.ticket_type_id).update(
                reserved_count=F('reserved_count') - item.quantity,
                sold_count=F('sold_count') + item.quantity,
            )
    order.status = Order.STATUS_PAID
    order.paid_at = paid_at
    if reference:
        order.paystack_reference = reference
    order.save(update_fields=['status', 'paid_at', 'paystack_reference', 'updated_at'])
    _issue_tickets(order)

    from .tasks import send_order_confirmation
    order_id = order.pk
    transaction.on_commit(lambda: send_order_confirmation.delay(order_id))


def _reacquire_stock(order):
    """Paiement recu apres expiration : on reprend le stock s'il est encore disponible."""
    items = list(order.items.order_by('ticket_type_id'))
    ticket_types = {
        tt.pk: tt
        for tt in TicketType.objects.select_for_update().filter(pk__in=[i.ticket_type_id for i in items]).order_by('pk')
    }
    if any(ticket_types[i.ticket_type_id].available < i.quantity for i in items):
        return False
    for item in items:
        TicketType.objects.filter(pk=item.ticket_type_id).update(sold_count=F('sold_count') + item.quantity)
    if order.promo_code_id:
        PromoCode.objects.filter(
            Q(max_uses__isnull=True) | Q(uses__lt=F('max_uses')), pk=order.promo_code_id
        ).update(uses=F('uses') + 1)
    return True


def fulfill_order(order_id, *, amount_subunits, currency, reference, paid_at=None):
    """Idempotent : un webhook rejoue ou une double verification ne cree jamais de billets en double."""
    paid_at = paid_at or timezone.now()
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if order is None or order.status in (Order.STATUS_PAID, Order.STATUS_NEEDS_REFUND, Order.STATUS_REFUNDED):
            return order

        expected = int(order.total) * 100
        if amount_subunits != expected or (currency or '').upper() != settings.PAYSTACK_CURRENCY.upper():
            logger.warning('Montant de paiement inattendu pour la commande %s', order.public_id)
            if order.status == Order.STATUS_PENDING:
                _release_stock(order)
            order.status = Order.STATUS_NEEDS_REFUND
            order.paystack_reference = reference or order.paystack_reference
            order.save(update_fields=['status', 'paystack_reference', 'updated_at'])
            return order

        if order.status == Order.STATUS_PENDING:
            _mark_paid(order, reference, paid_at, from_reservation=True)
        elif _reacquire_stock(order):
            _mark_paid(order, reference, paid_at, from_reservation=False)
        else:
            logger.warning('Paiement recu pour une commande expiree sans stock : %s', order.public_id)
            order.status = Order.STATUS_NEEDS_REFUND
            order.paystack_reference = reference or order.paystack_reference
            order.save(update_fields=['status', 'paystack_reference', 'updated_at'])

    invalidate_event_caches(order.event)
    return order


def _release_stock(order):
    for item in order.items.order_by('ticket_type_id'):
        TicketType.objects.filter(pk=item.ticket_type_id).update(reserved_count=F('reserved_count') - item.quantity)
    if order.promo_code_id:
        PromoCode.objects.filter(pk=order.promo_code_id, uses__gt=0).update(uses=F('uses') - 1)


def release_order(order_id, new_status, *, skip_locked=False):
    """Libere la reservation d'une commande en attente. Sans effet si elle n'est plus en attente."""
    with transaction.atomic():
        qs = Order.objects.select_for_update(skip_locked=skip_locked).filter(pk=order_id, status=Order.STATUS_PENDING)
        order = qs.first()
        if order is None:
            return None
        _release_stock(order)
        order.status = new_status
        order.save(update_fields=['status', 'updated_at'])
    invalidate_event_caches(order.event)
    return order

