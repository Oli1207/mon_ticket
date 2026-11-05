import logging

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from events.models import Event

from .models import Order, Ticket
from .services import invalidate_event_caches, release_order

logger = logging.getLogger(__name__)

EXPIRE_BATCH_SIZE = 500
EXPIRE_MAX_BATCHES = 20
EXPIRE_LOCK_KEY = 'task_lock:expire_pending_orders'


@shared_task
def expire_pending_orders():
    """Expire les commandes en attente echues et libere leur stock. Idempotent, par lots."""
    if not cache.add(EXPIRE_LOCK_KEY, 1, 55):
        return 0
    expired = 0
    try:
        for _ in range(EXPIRE_MAX_BATCHES):
            ids = list(
                Order.objects.filter(status=Order.STATUS_PENDING, expires_at__lte=timezone.now())
                .order_by('expires_at')
                .values_list('pk', flat=True)[:EXPIRE_BATCH_SIZE]
            )
            for order_id in ids:
                if release_order(order_id, Order.STATUS_EXPIRED, skip_locked=True) is not None:
                    expired += 1
            if len(ids) < EXPIRE_BATCH_SIZE:
                break
    finally:
        cache.delete(EXPIRE_LOCK_KEY)
    if expired:
        logger.info('%s commande(s) expiree(s)', expired)
    return expired


@shared_task(bind=True, max_retries=5, default_retry_delay=60)
def send_order_confirmation(self, order_id):
    with transaction.atomic():
        order = (
            Order.objects.select_for_update(of=('self',))
            .select_related('event')
            .filter(pk=order_id, status=Order.STATUS_PAID, confirmation_sent_at__isnull=True)
            .first()
        )
        if order is None:
            return
        link = f'{settings.FRONTEND_URL}/orders/{order.public_id}#token={order.access_token}'
        ticket_count = order.tickets.count()
        try:
            send_mail(
                subject=f'Tes billets pour {order.event.title}',
                message=(
                    f'Bonjour {order.full_name},\n\n'
                    f'Merci pour ta commande {order.public_id} : {ticket_count} billet(s) pour '
                    f'{order.event.title}, le {timezone.localtime(order.event.start_at):%d/%m/%Y a %H:%M} '
                    f'({order.event.venue_name}, {order.event.city}).\n\n'
                    f'Retrouve tes billets et leurs QR codes ici : {link}\n\n'
                    'Ne partage pas ce lien : il donne acces a tes billets.\n\n'
                    "L'equipe Mon Ticket"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[order.email],
            )
        except Exception as exc:
            raise self.retry(exc=exc)
        order.confirmation_sent_at = timezone.now()
        order.save(update_fields=['confirmation_sent_at', 'updated_at'])


@shared_task
def cancel_event_orders(event_id):
    """Evenement annule : billets invalides et commandes payees marquees a rembourser. Idempotent, par lots."""
    while True:
        ids = list(
            Order.objects.filter(event_id=event_id, status=Order.STATUS_PAID)
            .values_list('pk', flat=True)[:EXPIRE_BATCH_SIZE]
        )
        if not ids:
            break
        with transaction.atomic():
            Ticket.objects.filter(order_id__in=ids).exclude(status=Ticket.STATUS_CANCELLED).update(
                status=Ticket.STATUS_CANCELLED
            )
            Order.objects.filter(pk__in=ids, status=Order.STATUS_PAID).update(
                status=Order.STATUS_NEEDS_REFUND, updated_at=timezone.now()
            )
    for order_id in Order.objects.filter(event_id=event_id, status=Order.STATUS_PENDING).values_list('pk', flat=True).iterator():
        release_order(order_id, Order.STATUS_CANCELLED, skip_locked=True)
    event = Event.objects.filter(pk=event_id).first()
    if event is not None:
        invalidate_event_caches(event)
