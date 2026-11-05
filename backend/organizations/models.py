from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from config.validators import IMAGE_VALIDATORS

from .capabilities import ORG_ROLES, ROLE_DEFAULTS, resolve_capabilities


class Organization(models.Model):
    KIND_CHOICES = (
        ('promoter', 'Promoteur'),
        ('venue', 'Salle / lieu'),
        ('label', 'Label'),
        ('collective', 'Collectif'),
        ('association', 'Association'),
        ('company', 'Entreprise'),
    )
    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_SUSPENDED = 'suspended'
    STATUS_CHOICES = (
        (STATUS_PENDING, 'En attente'),
        (STATUS_APPROVED, 'Approuvee'),
        (STATUS_REJECTED, 'Refusee'),
        (STATUS_SUSPENDED, 'Suspendue'),
    )

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=80, unique=True)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    # PROTECT : supprimer un compte ne doit jamais effacer l'historique financier d'une organisation.
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='owned_organizations')
    logo = models.ImageField(upload_to='org_logos/', null=True, blank=True, validators=IMAGE_VALIDATORS)
    bio = models.TextField(blank=True)
    website = models.URLField(blank=True)
    social_links = models.JSONField(default=dict, blank=True)
    contact_email = models.EmailField(blank=True)
    paystack_subaccount_code = models.CharField(max_length=100, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=['status', '-created_at'], name='org_status_created_idx'),
        ]

    def __str__(self):
        return self.name

    @property
    def is_approved(self):
        return self.status == self.STATUS_APPROVED


class Membership(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=20, choices=ORG_ROLES)
    custom_caps = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('created_at',)
        constraints = [
            models.UniqueConstraint(fields=['organization', 'user'], name='uniq_membership_org_user'),
        ]
        indexes = [
            models.Index(fields=['user', 'is_active'], name='membership_user_active_idx'),
        ]

    def __str__(self):
        return f'{self.user} @ {self.organization} ({self.role})'

    @property
    def capabilities(self):
        return resolve_capabilities(ROLE_DEFAULTS[self.role], self.custom_caps)

    def has_cap(self, cap):
        return self.is_active and cap in self.capabilities


class Invitation(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_ACCEPTED = 'accepted'
    STATUS_REVOKED = 'revoked'
    STATUS_CHOICES = (
        (STATUS_PENDING, 'En attente'),
        (STATUS_ACCEPTED, 'Acceptee'),
        (STATUS_REVOKED, 'Revoquee'),
    )

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='invitations')
    email = models.EmailField()
    role = models.CharField(max_length=20, choices=ORG_ROLES)
    custom_caps = models.JSONField(default=dict, blank=True)
    # Seul le hash est stocke : une fuite de la base ne donne pas de lien d'invitation utilisable.
    token_hash = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=['organization', 'status'], name='invitation_org_status_idx'),
            models.Index(fields=['organization', 'email'], name='invitation_org_email_idx'),
        ]

    def __str__(self):
        return f'{self.email} -> {self.organization}'


class AuditLog(models.Model):
    """Journal append-only des actions sensibles sur une organisation."""

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, null=True, blank=True, related_name='audit_logs'
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    action = models.CharField(max_length=60)
    target_type = models.CharField(max_length=60, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    target_label = models.CharField(max_length=200, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)
        indexes = [
            models.Index(fields=['organization', '-created_at'], name='auditlog_org_created_idx'),
            models.Index(fields=['action'], name='auditlog_action_idx'),
            models.Index(fields=['-created_at'], name='auditlog_created_idx'),
        ]

    def __str__(self):
        return f'{self.action} par {self.actor_id} ({self.created_at:%Y-%m-%d %H:%M})'

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("Le journal d'audit est immuable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Le journal d'audit est immuable.")
