from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.db import models

from config.validators import IMAGE_VALIDATORS
from organizations.capabilities import STAFF_ROLE_DEFAULTS, resolve_capabilities
from organizations.models import Organization


class EventQuerySet(models.QuerySet):
    def public(self):
        return self.filter(
            status=Event.STATUS_PUBLISHED,
            organization__status=Organization.STATUS_APPROVED,
        )


class Event(models.Model):
    TYPE_CHOICES = (
        ('concert', 'Concert'),
        ('festival', 'Festival'),
        ('comedy', 'Humour'),
        ('theatre', 'Theatre'),
        ('party', 'Soiree'),
        ('conference', 'Conference'),
        ('workshop', 'Atelier'),
        ('exhibition', 'Exposition'),
        ('other', 'Autre'),
    )
    STATUS_DRAFT = 'draft'
    STATUS_PUBLISHED = 'published'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = (
        (STATUS_DRAFT, 'Brouillon'),
        (STATUS_PUBLISHED, 'Publie'),
        (STATUS_CANCELLED, 'Annule'),
    )

    # PROTECT : un evenement avec des commandes ne disparait jamais en cascade.
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name='events')
    title = models.CharField(max_length=300)
    slug = models.SlugField(max_length=80, unique=True)
    description = models.TextField(blank=True)
    event_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='other')
    poster = models.ImageField(upload_to='posters/', null=True, blank=True, validators=IMAGE_VALIDATORS)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField(null=True, blank=True)
    venue_name = models.CharField(max_length=200)
    venue_address = models.CharField(max_length=500, blank=True)
    city = models.CharField(max_length=100)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    min_age = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    is_featured = models.BooleanField(default=False)
    tags = models.JSONField(default=list, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = EventQuerySet.as_manager()

    class Meta:
        ordering = ('start_at',)
        indexes = [
            models.Index(fields=['status', 'start_at'], name='event_status_start_idx'),
            models.Index(fields=['status', 'is_featured', 'start_at'], name='event_featured_start_idx'),
            models.Index(fields=['status', 'city', 'start_at'], name='event_city_start_idx'),
            models.Index(fields=['status', 'event_type', 'start_at'], name='event_type_start_idx'),
            models.Index(fields=['organization', '-start_at'], name='event_org_start_idx'),
            # Recherche plein texte tolerante (ILIKE '%...%') sans scan complet.
            GinIndex(fields=['title'], opclasses=['gin_trgm_ops'], name='event_title_trgm_idx'),
        ]

    def __str__(self):
        return self.title

    @property
    def is_published(self):
        return self.status == self.STATUS_PUBLISHED


class EventStaff(models.Model):
    ROLE_CHOICES = (
        ('manager', 'Manager'),
        ('scanner', 'Scanner'),
        ('vip_host', 'Hote VIP'),
        ('security', 'Securite'),
    )

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='staff')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='event_staff_roles'
    )
    email = models.EmailField()
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    gate = models.CharField(max_length=50, blank=True)
    ticket_types = models.ManyToManyField('ticketing.TicketType', blank=True, related_name='+')
    custom_caps = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('created_at',)
        verbose_name = 'staff evenement'
        verbose_name_plural = 'staff evenement'
        constraints = [
            models.UniqueConstraint(fields=['event', 'user'], name='uniq_eventstaff_event_user'),
        ]
        indexes = [
            models.Index(fields=['user', 'is_active'], name='eventstaff_user_active_idx'),
        ]

    def __str__(self):
        return f'{self.email} - {self.event} ({self.role})'

    @property
    def capabilities(self):
        return resolve_capabilities(STAFF_ROLE_DEFAULTS[self.role], self.custom_caps)
