import re
from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from .models import (
    EventTicketing,
    Order,
    OrderItem,
    PartnerCode,
    PricingChange,
    PricingSettings,
    PromoCode,
    ScanLog,
    Ticket,
    TicketType,
)
from .pricing import buyer_fee_for, resolve_commission_percent

PHONE_RE = re.compile(r'^\+?[0-9 ]{6,20}$')
CODE_RE = re.compile(r'^[A-Z0-9_-]{3,30}$')
MAX_PRICE = Decimal('10000000')
DESCRIPTION_MAX_LENGTH = 2000


class FcfaField(serializers.DecimalField):
    """Montant FCFA : entier en JSON, DecimalField en base."""

    def __init__(self, **kwargs):
        kwargs.setdefault('max_digits', 10)
        kwargs.setdefault('decimal_places', 0)
        super().__init__(**kwargs)

    def to_representation(self, value):
        return int(value) if value is not None else None


# --------------------------------------------------------------------------- public


class PublicTicketTypeSerializer(serializers.ModelSerializer):
    price = FcfaField(read_only=True)
    buyer_fee = serializers.SerializerMethodField()
    available = serializers.IntegerField(read_only=True)
    is_on_sale = serializers.SerializerMethodField()

    class Meta:
        model = TicketType
        fields = (
            'id', 'name', 'description', 'price', 'buyer_fee', 'available', 'max_per_order',
            'sales_start', 'sales_end', 'is_on_sale',
        )
        read_only_fields = fields

    def get_buyer_fee(self, obj):
        ctx = self.context
        ticketing = ctx.get('ticketing')
        if ticketing is None:
            return 0
        pct = resolve_commission_percent(ctx['pricing'], ticketing, obj, ctx['now'])
        return int(buyer_fee_for(obj.price, pct, ticketing.fees_mode))

    def get_is_on_sale(self, obj):
        ticketing = self.context.get('ticketing')
        return bool(ticketing and ticketing.sales_open and obj.is_on_sale(self.context['now']) and obj.available > 0)


class OrderItemInputSerializer(serializers.Serializer):
    ticket_type_id = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1, max_value=100)


class OrderCreateSerializer(serializers.Serializer):
    event = serializers.SlugField(max_length=80)
    items = OrderItemInputSerializer(many=True, allow_empty=False, max_length=20)
    email = serializers.EmailField(max_length=254)
    phone = serializers.CharField(max_length=20)
    full_name = serializers.CharField(min_length=2, max_length=200)
    promo_code = serializers.CharField(max_length=30, required=False, allow_blank=True, default='')

    def validate_email(self, value):
        return value.strip().lower()

    def validate_phone(self, value):
        if not PHONE_RE.match(value):
            raise serializers.ValidationError('Numero de telephone invalide. Exemple : +225 07 00 00 00 00.')
        return value.replace(' ', '')

    def validate_full_name(self, value):
        return ' '.join(value.split())

    def validate_promo_code(self, value):
        return value.strip().upper()

    def validate_items(self, value):
        ids = [item['ticket_type_id'] for item in value]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError('Chaque type de billet ne doit apparaitre qu\'une fois.')
        return value


class OrderEventSerializer(serializers.Serializer):
    slug = serializers.CharField()
    title = serializers.CharField()
    start_at = serializers.DateTimeField()
    end_at = serializers.DateTimeField()
    venue_name = serializers.CharField()
    venue_address = serializers.CharField()
    city = serializers.CharField()
    poster = serializers.ImageField()


class OrderItemSerializer(serializers.ModelSerializer):
    ticket_type = serializers.CharField(source='ticket_type.name')
    unit_price = FcfaField()

    class Meta:
        model = OrderItem
        fields = ('ticket_type', 'quantity', 'unit_price')
        read_only_fields = fields


class TicketSerializer(serializers.ModelSerializer):
    ticket_type = serializers.CharField(source='ticket_type.name')
    qr_payload = serializers.SerializerMethodField()

    class Meta:
        model = Ticket
        fields = ('public_id', 'ticket_type', 'status', 'holder_name', 'used_at', 'qr_payload')
        read_only_fields = fields

    def get_qr_payload(self, obj):
        return obj.qr_payload if obj.status == Ticket.STATUS_VALID else None


class OrderSerializer(serializers.ModelSerializer):
    event = OrderEventSerializer(read_only=True)
    items = OrderItemSerializer(many=True, read_only=True)
    tickets = serializers.SerializerMethodField()
    subtotal = FcfaField()
    discount = FcfaField()
    service_fee = FcfaField()
    total = FcfaField()

    class Meta:
        model = Order
        fields = (
            'public_id', 'status', 'event', 'email', 'phone', 'full_name', 'items',
            'subtotal', 'discount', 'service_fee', 'total', 'fees_mode',
            'expires_at', 'paid_at', 'created_at', 'tickets',
        )
        read_only_fields = fields

    def get_tickets(self, obj):
        if obj.status != Order.STATUS_PAID:
            return []
        return TicketSerializer(obj.tickets.all(), many=True).data


class OrderCreatedSerializer(OrderSerializer):
    """Seule reponse qui contient l'access_token : le client le garde pour suivre sa commande."""

    class Meta(OrderSerializer.Meta):
        fields = OrderSerializer.Meta.fields + ('access_token',)
        read_only_fields = fields


# --------------------------------------------------------------------------- organisateur


class OrganizerTicketTypeSerializer(serializers.ModelSerializer):
    price = FcfaField(min_value=Decimal('0'), max_value=MAX_PRICE)
    available = serializers.IntegerField(read_only=True)
    effective_commission_percent = serializers.SerializerMethodField()

    class Meta:
        model = TicketType
        fields = (
            'id', 'name', 'description', 'price', 'quota', 'sold_count', 'reserved_count', 'available',
            'commission_override', 'effective_commission_percent', 'sales_start', 'sales_end',
            'max_per_order', 'sort_order', 'is_active', 'created_at',
        )
        read_only_fields = ('id', 'sold_count', 'reserved_count', 'commission_override', 'created_at')
        extra_kwargs = {
            'description': {'max_length': DESCRIPTION_MAX_LENGTH},
            'quota': {'min_value': 1, 'max_value': 1_000_000},
            'max_per_order': {'min_value': 1, 'max_value': 100},
            'sort_order': {'max_value': 10_000},
        }

    def get_effective_commission_percent(self, obj):
        ctx = self.context
        pct = resolve_commission_percent(ctx['pricing'], ctx.get('ticketing'), obj, timezone.now())
        return str(pct)

    def validate(self, attrs):
        start = attrs.get('sales_start', getattr(self.instance, 'sales_start', None))
        end = attrs.get('sales_end', getattr(self.instance, 'sales_end', None))
        if start and end and end <= start:
            raise serializers.ValidationError({'sales_end': ['La fin des ventes doit etre apres le debut.']})
        if self.instance is not None and 'quota' in attrs:
            committed = self.instance.sold_count + self.instance.reserved_count
            if attrs['quota'] < committed:
                raise serializers.ValidationError(
                    {'quota': [f'Le quota ne peut pas etre inferieur aux {committed} billets deja vendus ou reserves.']}
                )
        return attrs


class EventTicketingSerializer(serializers.ModelSerializer):
    partner_code = serializers.CharField(max_length=50, required=False, allow_blank=True, write_only=True)
    partner_code_label = serializers.CharField(source='partner_code.code', read_only=True, default=None)
    effective_commission_percent = serializers.SerializerMethodField()

    class Meta:
        model = EventTicketing
        fields = (
            'fees_mode', 'sales_open', 'partner_code', 'partner_code_label',
            'commission_override', 'effective_commission_percent', 'updated_at',
        )
        read_only_fields = ('commission_override', 'updated_at')

    def get_effective_commission_percent(self, obj):
        return str(resolve_commission_percent(self.context['pricing'], obj, None, timezone.now()))

    def validate_partner_code(self, value):
        return value.strip().upper()


class PromoCodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = PromoCode
        fields = ('id', 'code', 'kind', 'value', 'max_uses', 'uses', 'expires_at', 'is_active', 'created_at')
        read_only_fields = ('id', 'uses', 'created_at')
        extra_kwargs = {'max_uses': {'min_value': 1, 'max_value': 1_000_000}}

    def validate_code(self, value):
        value = value.strip().upper()
        if not CODE_RE.match(value):
            raise serializers.ValidationError('Le code doit contenir 3 a 30 caracteres : lettres, chiffres, - ou _.')
        return value

    def validate(self, attrs):
        kind = attrs.get('kind', getattr(self.instance, 'kind', None))
        value = attrs.get('value', getattr(self.instance, 'value', None))
        if value is not None:
            if value <= 0:
                raise serializers.ValidationError({'value': ['La valeur doit etre positive.']})
            if kind == PromoCode.KIND_PERCENT and value > 100:
                raise serializers.ValidationError({'value': ['Un pourcentage ne peut pas depasser 100.']})
            if kind == PromoCode.KIND_AMOUNT and (value != value.to_integral_value() or value > MAX_PRICE):
                raise serializers.ValidationError({'value': ['Le montant doit etre un nombre entier de FCFA.']})
        if self.instance is not None and 'max_uses' in attrs and attrs['max_uses'] is not None:
            if attrs['max_uses'] < self.instance.uses:
                raise serializers.ValidationError(
                    {'max_uses': [f'Ce code a deja ete utilise {self.instance.uses} fois.']}
                )
        return attrs


class OrganizerOrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    promo_code = serializers.CharField(source='promo_code.code', default=None)
    subtotal = FcfaField()
    discount = FcfaField()
    service_fee = FcfaField()
    total = FcfaField()
    organizer_net = FcfaField()
    commission_amount = FcfaField()

    class Meta:
        model = Order
        fields = (
            'public_id', 'status', 'email', 'phone', 'full_name', 'items', 'promo_code',
            'subtotal', 'discount', 'service_fee', 'total', 'organizer_net', 'commission_amount',
            'commission_percent', 'fees_mode', 'channel', 'created_at', 'paid_at',
        )
        read_only_fields = fields


class AttendeeSerializer(serializers.ModelSerializer):
    ticket_type = serializers.CharField(source='ticket_type.name')
    order = serializers.CharField(source='order.public_id')

    class Meta:
        model = Ticket
        fields = ('public_id', 'ticket_type', 'status', 'holder_name', 'holder_email', 'used_at', 'gate', 'order', 'created_at')
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self.context.get('include_contact'):
            data.pop('holder_email')
        return data


class ScanRequestSerializer(serializers.Serializer):
    payload = serializers.CharField(max_length=64, trim_whitespace=True)
    gate = serializers.CharField(max_length=50, required=False, allow_blank=True, default='')


class ScanLogSerializer(serializers.ModelSerializer):
    ticket = serializers.CharField(source='ticket.public_id', default=None)
    ticket_type = serializers.CharField(source='ticket.ticket_type.name', default=None)
    scanned_by = serializers.EmailField(source='scanned_by.email')

    class Meta:
        model = ScanLog
        fields = ('id', 'result', 'ticket', 'ticket_type', 'gate', 'scanned_by', 'created_at')
        read_only_fields = fields


# --------------------------------------------------------------------------- admin plateforme


class PricingSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = PricingSettings
        fields = (
            'commission_percent', 'mobile_money_fee_percent', 'card_fee_percent', 'reservation_minutes',
            'max_tickets_per_order', 'max_pending_per_email', 'max_pending_per_ip', 'updated_at',
        )
        read_only_fields = ('updated_at',)


class PartnerCodeSerializer(serializers.ModelSerializer):
    created_by = serializers.EmailField(source='created_by.email', read_only=True)

    class Meta:
        model = PartnerCode
        fields = (
            'id', 'code', 'label', 'commission_percent', 'expires_at', 'max_uses', 'uses',
            'is_active', 'created_by', 'created_at',
        )
        read_only_fields = ('id', 'uses', 'created_by', 'created_at')

    def validate_code(self, value):
        value = value.strip().upper()
        if not CODE_RE.match(value):
            raise serializers.ValidationError('Le code doit contenir 3 a 30 caracteres : lettres, chiffres, - ou _.')
        return value


class AdminEventPricingSerializer(serializers.Serializer):
    is_featured = serializers.BooleanField(required=False)
    commission_override = serializers.DecimalField(
        max_digits=5, decimal_places=2, min_value=Decimal('0'), max_value=Decimal('100'),
        required=False, allow_null=True,
    )


class AdminTicketTypeCommissionSerializer(serializers.Serializer):
    commission_override = serializers.DecimalField(
        max_digits=5, decimal_places=2, min_value=Decimal('0'), max_value=Decimal('100'), allow_null=True,
    )


class PricingChangeSerializer(serializers.ModelSerializer):
    changed_by = serializers.EmailField(source='changed_by.email', read_only=True)

    class Meta:
        model = PricingChange
        fields = ('id', 'changed_by', 'target', 'field', 'old_value', 'new_value', 'created_at')
        read_only_fields = fields
