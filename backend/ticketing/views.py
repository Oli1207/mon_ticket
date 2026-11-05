import json
import logging
import secrets

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Prefetch, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import generics, permissions, status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from config.net import get_client_ip
from events.mixins import OrgEventMixin
from events.models import Event, EventStaff
from organizations.audit import diff_fields, log_action
from organizations.permissions import HasOrgCapability, IsPlatformAdmin, get_active_membership

from . import paystack
from .audit import log_pricing_changes
from .models import (
    EventTicketing,
    Order,
    PartnerCode,
    PricingChange,
    PricingSettings,
    PromoCode,
    ScanLog,
    Ticket,
    TicketType,
)
from .serializers import (
    AdminEventPricingSerializer,
    AdminTicketTypeCommissionSerializer,
    AttendeeSerializer,
    EventTicketingSerializer,
    OrderCreatedSerializer,
    OrderCreateSerializer,
    OrderSerializer,
    OrganizerOrderSerializer,
    OrganizerTicketTypeSerializer,
    PartnerCodeSerializer,
    PricingChangeSerializer,
    PricingSettingsSerializer,
    PromoCodeSerializer,
    ScanLogSerializer,
    ScanRequestSerializer,
)
from .services import create_order, fulfill_order, invalidate_event_caches, release_order, stats_cache_key

logger = logging.getLogger(__name__)

ORDER_TOKEN_HEADER = 'HTTP_X_ORDER_TOKEN'
STATS_CACHE_TTL = 60


# =========================================================================== commandes publiques


def _order_queryset():
    return Order.objects.select_related('event').prefetch_related(
        'items__ticket_type',
        Prefetch('tickets', queryset=Ticket.objects.select_related('ticket_type').order_by('id')),
    )


def get_order_for_request(request, public_id):
    """Acces a une commande : son proprietaire connecte, ou quiconque detient son access_token."""
    order = _order_queryset().filter(public_id=public_id[:20]).first()
    if order is None:
        raise NotFound('Commande introuvable.')
    is_owner = request.user.is_authenticated and order.user_id == request.user.id
    if not is_owner and not order.check_access_token(request.META.get(ORDER_TOKEN_HEADER, '')):
        raise NotFound('Commande introuvable.')
    return order


class OrderCreateView(APIView):
    permission_classes = (permissions.AllowAny,)
    throttle_scope = 'order_create'

    def post(self, request):
        serializer = OrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        order = create_order(
            event_slug=data['event'],
            items=data['items'],
            email=data['email'],
            phone=data['phone'],
            full_name=data['full_name'],
            promo_code=data['promo_code'],
            client_ip=get_client_ip(request),
            user=request.user,
        )
        order = _order_queryset().get(pk=order.pk)
        return Response(OrderCreatedSerializer(order).data, status=status.HTTP_201_CREATED)


class OrderDetailView(APIView):
    permission_classes = (permissions.AllowAny,)
    throttle_scope = 'order_access'

    def get(self, request, public_id):
        return Response(OrderSerializer(get_order_for_request(request, public_id)).data)


class OrderPayView(APIView):
    permission_classes = (permissions.AllowAny,)
    throttle_scope = 'payment_init'

    def post(self, request, public_id):
        order = get_order_for_request(request, public_id)
        if order.status != Order.STATUS_PENDING:
            raise ValidationError({'detail': "Cette commande n'est plus en attente de paiement."})
        if order.expires_at <= timezone.now():
            raise ValidationError({'detail': 'Ta reservation a expire. Recommence ta commande.'})
        reference = f'{order.public_id}-{secrets.token_hex(4)}'
        callback_url = f'{settings.FRONTEND_URL}/orders/{order.public_id}/confirmation'
        data = paystack.initialize_transaction(order, reference, callback_url)
        Order.objects.filter(pk=order.pk, status=Order.STATUS_PENDING).update(
            paystack_reference=reference, updated_at=timezone.now()
        )
        return Response({
            'authorization_url': data.get('authorization_url'),
            'access_code': data.get('access_code'),
            'reference': reference,
        })


class OrderVerifyView(APIView):
    """Repli si le webhook tarde : verifie la transaction aupres de Paystack et finalise la commande."""

    permission_classes = (permissions.AllowAny,)
    throttle_scope = 'payment_init'

    def post(self, request, public_id):
        order = get_order_for_request(request, public_id)
        if order.status == Order.STATUS_PENDING and order.paystack_reference:
            data = paystack.verify_transaction(order.paystack_reference)
            if data.get('status') == 'success':
                fulfill_order(
                    order.pk,
                    amount_subunits=int(data.get('amount') or 0),
                    currency=data.get('currency', ''),
                    reference=data.get('reference', ''),
                    paid_at=parse_datetime(data.get('paid_at') or '') or None,
                )
            order = get_order_for_request(request, public_id)
        return Response(OrderSerializer(order).data)


class OrderCancelView(APIView):
    permission_classes = (permissions.AllowAny,)
    throttle_scope = 'order_access'

    def post(self, request, public_id):
        order = get_order_for_request(request, public_id)
        if release_order(order.pk, Order.STATUS_CANCELLED) is None:
            raise ValidationError({'detail': 'Seule une commande en attente de paiement peut etre annulee.'})
        return Response(OrderSerializer(get_order_for_request(request, public_id)).data)


class MyOrdersView(generics.ListAPIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = OrderSerializer

    def get_queryset(self):
        return _order_queryset().filter(user=self.request.user).order_by('-created_at')


class PaystackWebhookView(APIView):
    """Sans JWT : l'authenticite repose uniquement sur la signature HMAC SHA-512 du corps brut."""

    permission_classes = (permissions.AllowAny,)
    authentication_classes = ()
    throttle_scope = 'paystack_webhook'

    def post(self, request):
        raw_body = request.body
        if not paystack.is_valid_signature(raw_body, request.META.get('HTTP_X_PAYSTACK_SIGNATURE', '')):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        try:
            payload = json.loads(raw_body)
        except ValueError:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        if payload.get('event') != 'charge.success':
            return Response(status=status.HTTP_200_OK)

        data = payload.get('data') or {}
        reference = str(data.get('reference') or '')[:100]
        metadata = data.get('metadata') if isinstance(data.get('metadata'), dict) else {}
        public_id = str(metadata.get('order_public_id') or reference.split('-')[0])[:20]
        order = (
            Order.objects.filter(paystack_reference=reference).only('pk').first()
            if reference else None
        ) or Order.objects.filter(public_id=public_id).only('pk', 'public_id').first()
        # Une reference d'une autre commande ne peut pas finaliser celle-ci.
        if order is None or not reference.startswith(f'{order.public_id}-'):
            logger.warning('Webhook Paystack pour une commande inconnue')
            return Response(status=status.HTTP_200_OK)

        fulfill_order(
            order.pk,
            amount_subunits=int(data.get('amount') or 0),
            currency=str(data.get('currency') or ''),
            reference=reference,
            paid_at=parse_datetime(str(data.get('paid_at') or '')) or None,
        )
        return Response(status=status.HTTP_200_OK)


# =========================================================================== scan


class ScanView(APIView):
    """Controle d'acces : membres avec `checkin`, ou staff de l'evenement avec `checkin`."""

    permission_classes = (permissions.IsAuthenticated,)
    throttle_scope = 'scan'

    MESSAGES = {
        ScanLog.RESULT_ACCEPTED: 'Billet valide. Bienvenue !',
        ScanLog.RESULT_ALREADY_USED: 'Billet deja scanne.',
        ScanLog.RESULT_INVALID: 'QR code invalide.',
        ScanLog.RESULT_WRONG_EVENT: "Ce billet n'est pas pour cet evenement.",
        ScanLog.RESULT_NOT_ALLOWED: "Ce billet n'est pas autorise a cette porte.",
        ScanLog.RESULT_CANCELLED: 'Billet annule.',
    }

    def _allowed_ticket_types(self, request, event):
        """None = tous les types ; set = types autorises ; leve 403 si aucun droit de scan."""
        membership = get_active_membership(request, event.organization)
        if membership is not None and 'checkin' in membership.capabilities:
            return None
        staff = (
            EventStaff.objects.filter(event=event, user=request.user, is_active=True)
            .prefetch_related('ticket_types')
            .first()
        )
        if staff is None or 'checkin' not in staff.capabilities:
            raise PermissionDenied("Tu n'es pas autorise a scanner les billets de cet evenement.")
        allowed = {tt.pk for tt in staff.ticket_types.all()}
        return allowed or None

    def post(self, request, event_slug):
        event = get_object_or_404(Event.objects.select_related('organization'), slug=event_slug)
        allowed_types = self._allowed_ticket_types(request, event)
        serializer = ScanRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        gate = serializer.validated_data['gate']
        public_id = Ticket.parse_qr_payload(serializer.validated_data['payload'])

        ticket = None
        with transaction.atomic():
            if public_id is not None:
                ticket = (
                    Ticket.objects.select_for_update(of=('self',))
                    .select_related('ticket_type')
                    .filter(public_id=public_id)
                    .first()
                )
            if ticket is None:
                result = ScanLog.RESULT_INVALID
            elif ticket.event_id != event.pk:
                result = ScanLog.RESULT_WRONG_EVENT
            elif ticket.status == Ticket.STATUS_CANCELLED:
                result = ScanLog.RESULT_CANCELLED
            elif allowed_types is not None and ticket.ticket_type_id not in allowed_types:
                result = ScanLog.RESULT_NOT_ALLOWED
            elif ticket.status == Ticket.STATUS_USED:
                result = ScanLog.RESULT_ALREADY_USED
            else:
                result = ScanLog.RESULT_ACCEPTED
                ticket.status = Ticket.STATUS_USED
                ticket.used_at = timezone.now()
                ticket.checked_in_by = request.user
                ticket.gate = gate
                ticket.save(update_fields=['status', 'used_at', 'checked_in_by', 'gate'])
            ScanLog.objects.create(
                ticket=ticket if result != ScanLog.RESULT_WRONG_EVENT else None,
                event=event, scanned_by=request.user, result=result, gate=gate,
            )

        response = {'result': result, 'message': self.MESSAGES[result]}
        if ticket is not None and result != ScanLog.RESULT_WRONG_EVENT:
            response['ticket'] = {
                'public_id': ticket.public_id,
                'ticket_type': ticket.ticket_type.name,
                'holder_name': ticket.holder_name,
                'used_at': ticket.used_at,
                'gate': ticket.gate,
            }
        return Response(response)


# =========================================================================== organisateur


class OrgEventTicketingMixin(OrgEventMixin):
    permission_classes = (permissions.IsAuthenticated, HasOrgCapability)

    def get_serializer_context(self):
        event = self.get_event()
        return {
            **super().get_serializer_context(),
            'pricing': PricingSettings.load(),
            'ticketing': getattr(event, 'ticketing', None),
        }


class EventTicketingView(OrgEventTicketingMixin, generics.RetrieveUpdateAPIView):
    serializer_class = EventTicketingSerializer
    capability_map = {'GET': 'events.view', 'PATCH': 'events.manage'}
    http_method_names = ('get', 'patch')

    def get_throttles(self):
        if self.request.method == 'PATCH' and 'partner_code' in self.request.data:
            self.throttle_scope = 'partner_code'
        return super().get_throttles()

    def get_object(self):
        ticketing, _ = EventTicketing.objects.select_related('partner_code').get_or_create(event=self.get_event())
        return ticketing

    def _check_can_open_sales(self, event):
        if event.status != Event.STATUS_PUBLISHED:
            raise ValidationError({'sales_open': ["Publie l'evenement avant d'ouvrir la billetterie."]})
        if not event.organization.is_approved:
            raise ValidationError({'sales_open': ['Ton organisation doit etre validee avant de vendre des billets.']})
        active_types = TicketType.objects.filter(event=event, is_active=True)
        if not active_types.exists():
            raise ValidationError({'sales_open': ['Cree au moins un type de billet actif.']})
        if active_types.filter(price__gt=0).exists() and not event.organization.paystack_subaccount_code:
            raise ValidationError({'sales_open': ["Ton compte de reversement n'est pas encore configure. Contacte l'equipe Mon Ticket."]})

    def perform_update(self, serializer):
        event = self.get_event()
        data = dict(serializer.validated_data)
        ticketing = serializer.instance
        partner_code_raw = data.pop('partner_code', None)

        if data.get('sales_open') and not ticketing.sales_open:
            self._check_can_open_sales(event)

        with transaction.atomic():
            ticketing = EventTicketing.objects.select_for_update(of=('self',)).select_related('partner_code').get(pk=ticketing.pk)
            pricing_changes = diff_fields(ticketing, {k: v for k, v in data.items() if k == 'fees_mode'})
            if partner_code_raw is not None:
                old_code = ticketing.partner_code.code if ticketing.partner_code else None
                if partner_code_raw == '':
                    new_partner = None
                else:
                    now = timezone.now()
                    new_partner = PartnerCode.objects.filter(code=partner_code_raw).first()
                    if new_partner is None or not new_partner.is_usable(now):
                        raise ValidationError({'partner_code': ['Code partenaire invalide ou expire.']})
                    if new_partner.pk != ticketing.partner_code_id:
                        consumed = PartnerCode.objects.filter(
                            Q(max_uses__isnull=True) | Q(uses__lt=F('max_uses')), pk=new_partner.pk
                        ).update(uses=F('uses') + 1)
                        if not consumed:
                            raise ValidationError({'partner_code': ['Ce code partenaire a atteint sa limite.']})
                if (new_partner.code if new_partner else None) != old_code:
                    pricing_changes['partner_code'] = {'old': old_code, 'new': new_partner.code if new_partner else None}
                ticketing.partner_code = new_partner
            sales_changes = diff_fields(ticketing, {k: v for k, v in data.items() if k == 'sales_open'})
            for field, value in data.items():
                setattr(ticketing, field, value)
            ticketing.save()
            if pricing_changes:
                log_pricing_changes(self.request.user, f'event:{event.slug}', pricing_changes)
            if pricing_changes or sales_changes:
                log_action(self.request, 'ticketing.updated', organization=event.organization, target=event,
                           changes={**pricing_changes, **sales_changes})
        serializer.instance = ticketing
        invalidate_event_caches(event)


class TicketTypeListCreateView(OrgEventTicketingMixin, generics.ListCreateAPIView):
    serializer_class = OrganizerTicketTypeSerializer
    capability_map = {'GET': 'events.view', 'POST': 'events.manage'}

    def get_queryset(self):
        return TicketType.objects.filter(event=self.get_event()).order_by('sort_order', 'id')

    def perform_create(self, serializer):
        event = self.get_event()
        if event.status == Event.STATUS_CANCELLED:
            raise ValidationError({'detail': 'Cet evenement est annule.'})
        if TicketType.objects.filter(event=event).count() >= 50:
            raise ValidationError({'detail': 'Maximum 50 types de billets par evenement.'})
        tt = serializer.save(event=event)
        log_action(self.request, 'ticket_type.created', organization=event.organization, target=tt,
                   changes={'price': int(tt.price), 'quota': tt.quota})
        invalidate_event_caches(event)


class TicketTypeDetailView(OrgEventTicketingMixin, generics.RetrieveUpdateDestroyAPIView):
    serializer_class = OrganizerTicketTypeSerializer
    capability_map = {'GET': 'events.view', 'PATCH': 'events.manage', 'DELETE': 'events.manage'}
    http_method_names = ('get', 'patch', 'delete')

    def get_queryset(self):
        return TicketType.objects.filter(event=self.get_event())

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        with transaction.atomic():
            # Verrou : le controle quota >= vendus + reserves doit voir des compteurs a jour.
            locked = TicketType.objects.select_for_update().get(pk=instance.pk)
            serializer = self.get_serializer(locked, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            changes = diff_fields(locked, serializer.validated_data)
            tt = serializer.save()
            if changes:
                log_action(request, 'ticket_type.updated', organization=self.get_organization(), target=tt, changes=changes)
        invalidate_event_caches(self.get_event())
        return Response(serializer.data)

    def perform_destroy(self, tt):
        if tt.order_items.exists():
            tt.is_active = False
            tt.save(update_fields=['is_active', 'updated_at'])
            action = 'ticket_type.deactivated'
        else:
            tt.delete()
            action = 'ticket_type.deleted'
        log_action(self.request, action, organization=self.get_organization(), target=tt)
        invalidate_event_caches(self.get_event())


class PromoCodeListCreateView(OrgEventMixin, generics.ListCreateAPIView):
    permission_classes = (permissions.IsAuthenticated, HasOrgCapability)
    serializer_class = PromoCodeSerializer
    capability_map = {'GET': 'marketing.view', 'POST': 'marketing.manage'}

    def get_queryset(self):
        return PromoCode.objects.filter(event=self.get_event()).order_by('-created_at')

    def perform_create(self, serializer):
        event = self.get_event()
        try:
            with transaction.atomic():
                promo = serializer.save(event=event)
        except IntegrityError:
            raise ValidationError({'code': ['Ce code existe deja pour cet evenement.']})
        log_action(self.request, 'promo_code.created', organization=event.organization, target=promo,
                   changes={'kind': promo.kind, 'value': str(promo.value)})


class PromoCodeDetailView(OrgEventMixin, generics.RetrieveUpdateDestroyAPIView):
    permission_classes = (permissions.IsAuthenticated, HasOrgCapability)
    serializer_class = PromoCodeSerializer
    capability_map = {'GET': 'marketing.view', 'PATCH': 'marketing.manage', 'DELETE': 'marketing.manage'}
    http_method_names = ('get', 'patch', 'delete')

    def get_queryset(self):
        return PromoCode.objects.filter(event=self.get_event())

    def perform_update(self, serializer):
        changes = diff_fields(serializer.instance, serializer.validated_data)
        try:
            with transaction.atomic():
                promo = serializer.save()
        except IntegrityError:
            raise ValidationError({'code': ['Ce code existe deja pour cet evenement.']})
        if changes:
            log_action(self.request, 'promo_code.updated', organization=self.get_organization(), target=promo, changes=changes)

    def perform_destroy(self, promo):
        if promo.uses > 0:
            promo.is_active = False
            promo.save(update_fields=['is_active'])
            action = 'promo_code.deactivated'
        else:
            promo.delete()
            action = 'promo_code.deleted'
        log_action(self.request, action, organization=self.get_organization(), target=promo)


class OrganizerOrderListView(OrgEventMixin, generics.ListAPIView):
    permission_classes = (permissions.IsAuthenticated, HasOrgCapability)
    serializer_class = OrganizerOrderSerializer
    capability_map = {'GET': 'sales.view'}

    def get_queryset(self):
        qs = Order.objects.filter(event=self.get_event())
        status_filter = self.request.query_params.get('status')
        if status_filter:
            if status_filter not in dict(Order.STATUS_CHOICES):
                raise ValidationError({'status': ['Statut inconnu.']})
            qs = qs.filter(status=status_filter)
        return qs.select_related('promo_code').prefetch_related('items__ticket_type').order_by('-created_at', '-pk')


class AttendeeListView(OrgEventMixin, generics.ListAPIView):
    permission_classes = (permissions.IsAuthenticated, HasOrgCapability)
    serializer_class = AttendeeSerializer
    capability_map = {'GET': 'attendees.view'}

    def get_serializer_context(self):
        membership = self.get_membership()
        return {**super().get_serializer_context(), 'include_contact': 'attendees.contact' in membership.capabilities}

    def get_queryset(self):
        qs = Ticket.objects.filter(event=self.get_event())
        params = self.request.query_params
        if status_filter := params.get('status'):
            if status_filter not in dict(Ticket.STATUS_CHOICES):
                raise ValidationError({'status': ['Statut inconnu.']})
            qs = qs.filter(status=status_filter)
        if ticket_type := params.get('ticket_type'):
            if not ticket_type.isdigit():
                raise ValidationError({'ticket_type': ['Identifiant invalide.']})
            qs = qs.filter(ticket_type_id=int(ticket_type))
        return qs.select_related('ticket_type', 'order').order_by('created_at', 'id')


class EventStatsView(OrgEventMixin, APIView):
    permission_classes = (permissions.IsAuthenticated, HasOrgCapability)
    capability_map = {'GET': 'sales.view'}

    def get(self, request, org_slug, event_slug):
        event = self.get_event()
        key = stats_cache_key(event.pk)
        data = cache.get(key)
        if data is None:
            data = self._compute(event)
            cache.set(key, data, STATS_CACHE_TTL)
        return Response(data)

    def _compute(self, event):
        paid = Order.objects.filter(event=event, status=Order.STATUS_PAID).aggregate(
            orders=Count('pk'),
            gross=Sum('total'),
            organizer_net=Sum('organizer_net'),
            commission=Sum('commission_amount'),
            discounts=Sum('discount'),
        )
        by_status = dict(
            Order.objects.filter(event=event).values_list('status').annotate(n=Count('pk')).order_by()
        )
        tickets = Ticket.objects.filter(event=event).aggregate(
            issued=Count('pk', filter=~Q(status=Ticket.STATUS_CANCELLED)),
            checked_in=Count('pk', filter=Q(status=Ticket.STATUS_USED)),
        )
        ticket_types = [
            {
                'id': tt.pk, 'name': tt.name, 'price': int(tt.price), 'quota': tt.quota,
                'sold': tt.sold_count, 'reserved': tt.reserved_count, 'available': tt.available,
                'revenue': int(tt.price) * tt.sold_count,
            }
            for tt in TicketType.objects.filter(event=event).order_by('sort_order', 'id')
        ]
        return {
            'orders_paid': paid['orders'],
            'gross_revenue': int(paid['gross'] or 0),
            'organizer_net': int(paid['organizer_net'] or 0),
            'commission': int(paid['commission'] or 0),
            'discounts': int(paid['discounts'] or 0),
            'orders_by_status': by_status,
            'tickets_issued': tickets['issued'],
            'tickets_checked_in': tickets['checked_in'],
            'ticket_types': ticket_types,
            'computed_at': timezone.now().isoformat(),
        }


class ScanLogListView(OrgEventMixin, generics.ListAPIView):
    permission_classes = (permissions.IsAuthenticated, HasOrgCapability)
    serializer_class = ScanLogSerializer
    capability_map = {'GET': 'checkin'}

    def get_queryset(self):
        return (
            ScanLog.objects.filter(event=self.get_event())
            .select_related('ticket__ticket_type', 'scanned_by')
            .order_by('-created_at', '-pk')
        )


# =========================================================================== admin plateforme


class AdminPricingSettingsView(APIView):
    permission_classes = (IsPlatformAdmin,)
    throttle_scope = 'admin_sensitive'

    def get(self, request):
        return Response(PricingSettingsSerializer(PricingSettings.load()).data)

    def patch(self, request):
        with transaction.atomic():
            PricingSettings.objects.get_or_create(pk=1)
            current = PricingSettings.objects.select_for_update().get(pk=1)
            serializer = PricingSettingsSerializer(current, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            changes = diff_fields(current, serializer.validated_data)
            serializer.save()
            log_pricing_changes(request.user, 'global', changes)
        return Response(serializer.data)


class AdminPartnerCodeListCreateView(generics.ListCreateAPIView):
    permission_classes = (IsPlatformAdmin,)
    serializer_class = PartnerCodeSerializer
    throttle_scope = 'admin_sensitive'
    queryset = PartnerCode.objects.select_related('created_by').order_by('-created_at')

    def perform_create(self, serializer):
        try:
            with transaction.atomic():
                partner = serializer.save(created_by=self.request.user)
                log_pricing_changes(self.request.user, f'partner:{partner.code}', {
                    'commission_percent': {'old': None, 'new': partner.commission_percent},
                })
        except IntegrityError:
            raise ValidationError({'code': ['Ce code existe deja.']})


class AdminPartnerCodeDetailView(generics.RetrieveUpdateAPIView):
    permission_classes = (IsPlatformAdmin,)
    serializer_class = PartnerCodeSerializer
    throttle_scope = 'admin_sensitive'
    queryset = PartnerCode.objects.select_related('created_by')
    http_method_names = ('get', 'patch')

    def perform_update(self, serializer):
        if 'code' in serializer.validated_data and serializer.validated_data['code'] != serializer.instance.code:
            raise ValidationError({'code': ["Le code ne peut pas etre modifie. Cree un nouveau code."]})
        changes = diff_fields(serializer.instance, serializer.validated_data)
        with transaction.atomic():
            partner = serializer.save()
            log_pricing_changes(self.request.user, f'partner:{partner.code}', changes)
        for event_ticketing in partner.event_ticketings.select_related('event'):
            invalidate_event_caches(event_ticketing.event)


class AdminEventPricingView(APIView):
    permission_classes = (IsPlatformAdmin,)
    throttle_scope = 'admin_sensitive'

    def patch(self, request, slug):
        serializer = AdminEventPricingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            event = get_object_or_404(Event.objects.select_for_update(), slug=slug)
            ticketing, _ = EventTicketing.objects.get_or_create(event=event)
            if 'is_featured' in data:
                featured_changes = diff_fields(event, {'is_featured': data['is_featured']})
                event.is_featured = data['is_featured']
                event.save(update_fields=['is_featured', 'updated_at'])
                if featured_changes:
                    log_action(request, 'event.featured', organization=event.organization,
                               target=event, changes=featured_changes)
            if 'commission_override' in data:
                changes = diff_fields(ticketing, {'commission_override': data['commission_override']})
                ticketing.commission_override = data['commission_override']
                ticketing.save(update_fields=['commission_override', 'updated_at'])
                log_pricing_changes(request.user, f'event:{event.slug}', changes)
        invalidate_event_caches(event)
        return Response({
            'slug': event.slug,
            'is_featured': event.is_featured,
            'commission_override': None if ticketing.commission_override is None else str(ticketing.commission_override),
        })


class AdminTicketTypeCommissionView(APIView):
    permission_classes = (IsPlatformAdmin,)
    throttle_scope = 'admin_sensitive'

    def patch(self, request, pk):
        serializer = AdminTicketTypeCommissionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        value = serializer.validated_data['commission_override']
        with transaction.atomic():
            tt = get_object_or_404(TicketType.objects.select_for_update(of=('self',)).select_related('event'), pk=pk)
            changes = diff_fields(tt, {'commission_override': value})
            tt.commission_override = value
            tt.save(update_fields=['commission_override', 'updated_at'])
            log_pricing_changes(request.user, f'ticket_type:{tt.pk}', changes)
        invalidate_event_caches(tt.event)
        return Response({'id': tt.pk, 'commission_override': None if value is None else str(value)})


class AdminPricingChangeListView(generics.ListAPIView):
    permission_classes = (IsPlatformAdmin,)
    serializer_class = PricingChangeSerializer
    queryset = PricingChange.objects.select_related('changed_by').order_by('-created_at', '-pk')
