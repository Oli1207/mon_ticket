import hashlib
import hmac
import secrets
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q

from events.models import Event

PERCENT_VALIDATORS = [MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))]

PUBLIC_ID_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def generate_public_id(length=12):
    return ''.join(secrets.choice(PUBLIC_ID_ALPHABET) for _ in range(length))


def generate_access_token():
    return secrets.token_urlsafe(32)


class PricingSettings(models.Model):
    """Singleton (pk=1) des reglages globaux, mis en cache 5 minutes."""

    CACHE_KEY = 'pricing_settings:v1'
    CACHE_TTL = 300

    commission_percent = models.DecimalField(
        default=Decimal('6.00'), max_digits=5, decimal_places=2, validators=PERCENT_VALIDATORS
    )
    mobile_money_fee_percent = models.DecimalField(
        default=Decimal('1.50'), max_digits=5, decimal_places=2, validators=PERCENT_VALIDATORS
    )
    card_fee_percent = models.DecimalField(
        default=Decimal('1.50'), max_digits=5, decimal_places=2, validators=PERCENT_VALIDATORS
    )
    reservation_minutes = models.PositiveIntegerField(
        default=20, validators=[MinValueValidator(5), MaxValueValidator(120)]
    )
    max_tickets_per_order = models.PositiveIntegerField(
        default=10, validators=[MinValueValidator(1), MaxValueValidator(100)]
    )
    max_pending_per_email = models.PositiveIntegerField(
        default=2, validators=[MinValueValidator(1), MaxValueValidator(50)]
    )
    max_pending_per_ip = models.PositiveIntegerField(
        default=20, validators=[MinValueValidator(1), MaxValueValidator(1000)]
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'reglages tarifaires'
        verbose_name_plural = 'reglages tarifaires'

    def __str__(self):
        return 'Reglages tarifaires globaux'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete(self.CACHE_KEY)

    def delete(self, *args, **kwargs):
        raise ValidationError('Les reglages globaux ne peuvent pas etre supprimes.')

    @classmethod
    def load(cls):
        obj = cache.get(cls.CACHE_KEY)
        if obj is None:
            obj, _ = cls.objects.get_or_create(pk=1)
            cache.set(cls.CACHE_KEY, obj, cls.CACHE_TTL)
        return obj


class PartnerCode(models.Model):
    code = models.CharField(max_length=50, unique=True)
    label = models.CharField(max_length=200, blank=True)
    commission_percent = models.DecimalField(max_digits=5, decimal_places=2, validators=PERCENT_VALIDATORS)
    expires_at = models.DateTimeField(null=True, blank=True)
    max_uses = models.PositiveIntegerField(null=True, blank=True)
    uses = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return self.code

    def is_usable(self, now):
        return self.is_active and (self.expires_at is None or self.expires_at > now)


class EventTicketing(models.Model):
    FEES_BUYER = 'buyer'
    FEES_ORGANIZER = 'organizer'
    FEES_MODE_CHOICES = (
        (FEES_BUYER, "Frais a la charge de l'acheteur"),
        (FEES_ORGANIZER, "Frais a la charge de l'organisateur"),
    )

    event = models.OneToOneField(Event, on_delete=models.CASCADE, related_name='ticketing')
    fees_mode = models.CharField(max_length=10, choices=FEES_MODE_CHOICES, default=FEES_BUYER)
    commission_override = models.DecimalField(
        null=True, blank=True, max_digits=5, decimal_places=2, validators=PERCENT_VALIDATORS
    )
    partner_code = models.ForeignKey(
        PartnerCode, null=True, blank=True, on_delete=models.SET_NULL, related_name='event_ticketings'
    )
    sales_open = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'billetterie evenement'
        verbose_name_plural = 'billetteries evenements'

    def __str__(self):
        return f'Billetterie - {self.event}'


class TicketType(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='ticket_types')
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=0, validators=[MinValueValidator(Decimal('0'))])
    quota = models.PositiveIntegerField()
    sold_count = models.PositiveIntegerField(default=0)
    reserved_count = models.PositiveIntegerField(default=0)
    commission_override = models.DecimalField(
        null=True, blank=True, max_digits=5, decimal_places=2, validators=PERCENT_VALIDATORS
    )
    sales_start = models.DateTimeField(null=True, blank=True)
    sales_end = models.DateTimeField(null=True, blank=True)
    max_per_order = models.PositiveIntegerField(default=10, validators=[MinValueValidator(1)])
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('sort_order', 'id')
        indexes = [
            models.Index(fields=['event', 'sort_order'], name='tickettype_event_sort_idx'),
        ]
        constraints = [
            # Filet de securite en base : meme un bug applicatif ne peut pas survendre.
            models.CheckConstraint(
                condition=Q(sold_count__lte=F('quota') - F('reserved_count')),
                name='tickettype_no_oversell',
            ),
        ]

    def __str__(self):
        return f'{self.event} - {self.name}'

    @property
    def available(self):
        return max(self.quota - self.sold_count - self.reserved_count, 0)

    def is_on_sale(self, now):
        return (
            self.is_active
            and (self.sales_start is None or self.sales_start <= now)
            and (self.sales_end is None or self.sales_end > now)
        )


class PromoCode(models.Model):
    KIND_PERCENT = 'percent'
    KIND_AMOUNT = 'amount'
    KIND_CHOICES = ((KIND_PERCENT, 'Pourcentage'), (KIND_AMOUNT, 'Montant fixe'))

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='promo_codes')
    code = models.CharField(max_length=30)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    value = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0'))])
    max_uses = models.PositiveIntegerField(null=True, blank=True)
    uses = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)
        constraints = [
            models.UniqueConstraint(fields=['event', 'code'], name='uniq_promocode_event_code'),
            models.CheckConstraint(
                condition=Q(max_uses__isnull=True) | Q(uses__lte=F('max_uses')),
                name='promocode_uses_within_max',
            ),
        ]

    def __str__(self):
        return f'{self.code} ({self.event})'


class Order(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_PAID = 'paid'
    STATUS_EXPIRED = 'expired'
    STATUS_CANCELLED = 'cancelled'
    STATUS_REFUNDED = 'refunded'
    STATUS_NEEDS_REFUND = 'needs_refund'
    STATUS_CHOICES = (
        (STATUS_PENDING, 'En attente de paiement'),
        (STATUS_PAID, 'Payee'),
        (STATUS_EXPIRED, 'Expiree'),
        (STATUS_CANCELLED, 'Annulee'),
        (STATUS_REFUNDED, 'Remboursee'),
        (STATUS_NEEDS_REFUND, 'A rembourser'),
    )

    public_id = models.CharField(max_length=20, unique=True, default=generate_public_id)
    access_token = models.CharField(max_length=64, default=generate_access_token)
    # L'index composite (event, status, created_at) couvre aussi les recherches par event_id seul.
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name='orders', db_index=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders'
    )
    email = models.EmailField()
    phone = models.CharField(max_length=20)
    full_name = models.CharField(max_length=200)
    client_ip = models.GenericIPAddressField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    channel = models.CharField(max_length=20, default='web')
    promo_code = models.ForeignKey(PromoCode, null=True, blank=True, on_delete=models.SET_NULL, related_name='orders')
    subtotal = models.DecimalField(max_digits=10, decimal_places=0)
    discount = models.DecimalField(max_digits=10, decimal_places=0, default=0)
    service_fee = models.DecimalField(max_digits=10, decimal_places=0)
    total = models.DecimalField(max_digits=10, decimal_places=0)
    organizer_net = models.DecimalField(max_digits=10, decimal_places=0)
    commission_amount = models.DecimalField(max_digits=10, decimal_places=0)
    commission_percent = models.DecimalField(max_digits=5, decimal_places=2)
    fees_mode = models.CharField(max_length=10)
    paystack_reference = models.CharField(max_length=100, blank=True)
    expires_at = models.DateTimeField()
    paid_at = models.DateTimeField(null=True, blank=True)
    confirmation_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=['status', 'expires_at'], name='order_status_expires_idx'),
            models.Index(fields=['email', 'status'], name='order_email_status_idx'),
            models.Index(fields=['client_ip', 'status'], name='order_ip_status_idx'),
            models.Index(fields=['event', 'status', '-created_at'], name='order_event_status_idx'),
            models.Index(
                fields=['paystack_reference'], name='order_paystack_ref_idx',
                condition=~Q(paystack_reference=''),
            ),
        ]

    def __str__(self):
        return self.public_id

    def check_access_token(self, token):
        return bool(token) and hmac.compare_digest(self.access_token, token)


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    ticket_type = models.ForeignKey(TicketType, on_delete=models.PROTECT, related_name='order_items')
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(max_digits=10, decimal_places=0)
    commission_percent = models.DecimalField(max_digits=5, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['order', 'ticket_type'], name='uniq_orderitem_order_type'),
        ]

    def __str__(self):
        return f'{self.quantity} x {self.ticket_type.name}'


class Ticket(models.Model):
    QR_PREFIX = 'SPT1'

    STATUS_VALID = 'valid'
    STATUS_USED = 'used'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = (
        (STATUS_VALID, 'Valide'),
        (STATUS_USED, 'Utilise'),
        (STATUS_CANCELLED, 'Annule'),
    )

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='tickets')
    # L'index composite (event, status) couvre aussi les recherches par event_id seul.
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name='tickets', db_index=False)
    ticket_type = models.ForeignKey(TicketType, on_delete=models.PROTECT, related_name='tickets')
    public_id = models.CharField(max_length=20, unique=True, default=generate_public_id)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_VALID)
    holder_name = models.CharField(max_length=200, blank=True)
    holder_email = models.EmailField(blank=True)
    used_at = models.DateTimeField(null=True, blank=True)
    checked_in_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )
    gate = models.CharField(max_length=50, blank=True)
    transferred_from = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('created_at', 'id')
        indexes = [
            models.Index(fields=['event', 'status'], name='ticket_event_status_idx'),
        ]

    def __str__(self):
        return self.public_id

    @staticmethod
    def sign(public_id):
        return hmac.new(
            settings.QR_SECRET_KEY.encode(), public_id.encode(), hashlib.sha256
        ).hexdigest()[:12]

    @property
    def qr_payload(self):
        return f'{self.QR_PREFIX}.{self.public_id}.{self.sign(self.public_id)}'

    @classmethod
    def parse_qr_payload(cls, payload):
        """Renvoie le public_id si la signature est valide, sinon None."""
        if not isinstance(payload, str) or len(payload) > 64:
            return None
        parts = payload.strip().split('.')
        if len(parts) != 3 or parts[0] != cls.QR_PREFIX:
            return None
        _, public_id, signature = parts
        if not public_id or not hmac.compare_digest(cls.sign(public_id), signature):
            return None
        return public_id


class ScanLog(models.Model):
    RESULT_ACCEPTED = 'accepted'
    RESULT_ALREADY_USED = 'already_used'
    RESULT_INVALID = 'invalid'
    RESULT_WRONG_EVENT = 'wrong_event'
    RESULT_NOT_ALLOWED = 'not_allowed'
    RESULT_CANCELLED = 'cancelled'
    RESULT_CHOICES = (
        (RESULT_ACCEPTED, 'Accepte'),
        (RESULT_ALREADY_USED, 'Deja utilise'),
        (RESULT_INVALID, 'Invalide'),
        (RESULT_WRONG_EVENT, 'Mauvais evenement'),
        (RESULT_NOT_ALLOWED, 'Non autorise a cette porte'),
        (RESULT_CANCELLED, 'Annule'),
    )

    # Nullable : un QR forge ou illisible est journalise sans billet associe.
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, null=True, blank=True, related_name='scans')
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='scans', db_index=False)
    scanned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    result = models.CharField(max_length=20, choices=RESULT_CHOICES)
    gate = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=['event', '-created_at'], name='scanlog_event_created_idx'),
        ]

    def __str__(self):
        return f'{self.result} - {self.created_at:%H:%M:%S}'


class PricingChange(models.Model):
    """Journal immuable de toute modification de commission ou de reglage tarifaire."""

    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    target = models.CharField(max_length=200)
    field = models.CharField(max_length=100)
    old_value = models.CharField(max_length=200, blank=True)
    new_value = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=['-created_at'], name='pricingchange_created_idx'),
            models.Index(fields=['target'], name='pricingchange_target_idx'),
        ]

    def __str__(self):
        return f'{self.target}.{self.field}: {self.old_value} -> {self.new_value}'

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError('Le journal des changements tarifaires est immuable.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Le journal des changements tarifaires est immuable.')
