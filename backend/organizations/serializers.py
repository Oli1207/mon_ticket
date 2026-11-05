from urllib.parse import urlparse

from rest_framework import serializers

from .capabilities import (
    ASSIGNABLE_ROLES,
    CAPABILITIES,
    ROLE_DEFAULTS,
    resolve_capabilities,
    validate_caps_overrides,
)
from .models import AuditLog, Invitation, Membership, Organization

SOCIAL_NETWORKS = ('instagram', 'facebook', 'twitter', 'tiktok', 'youtube', 'linkedin', 'whatsapp')
BIO_MAX_LENGTH = 5000


def validate_social_links(value):
    if not isinstance(value, dict):
        raise serializers.ValidationError('Format invalide : un objet {"reseau": "url"} est attendu.')
    cleaned = {}
    for network, url in value.items():
        if network not in SOCIAL_NETWORKS:
            raise serializers.ValidationError(f'Reseau non pris en charge. Choix : {", ".join(SOCIAL_NETWORKS)}.')
        if not isinstance(url, str) or len(url) > 300:
            raise serializers.ValidationError(f'Lien {network} invalide (300 caracteres max).')
        if url and urlparse(url).scheme not in ('http', 'https'):
            raise serializers.ValidationError(f'Le lien {network} doit commencer par https://.')
        if url:
            cleaned[network] = url
    return cleaned


class UserSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    email = serializers.EmailField()
    full_name = serializers.CharField()


class PublicOrganizationSerializer(serializers.ModelSerializer):
    is_verified = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = ('name', 'slug', 'kind', 'logo', 'bio', 'website', 'social_links', 'is_verified')

    def get_is_verified(self, obj):
        return obj.verified_at is not None


class OrganizationSerializer(serializers.ModelSerializer):
    my_role = serializers.SerializerMethodField()
    my_capabilities = serializers.SerializerMethodField()
    has_payout_account = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = (
            'id', 'name', 'slug', 'kind', 'status', 'logo', 'bio', 'website', 'social_links',
            'contact_email', 'has_payout_account', 'verified_at', 'created_at',
            'my_role', 'my_capabilities',
        )
        read_only_fields = ('id', 'slug', 'status', 'verified_at', 'created_at')
        extra_kwargs = {'bio': {'max_length': BIO_MAX_LENGTH}}

    def validate_social_links(self, value):
        return validate_social_links(value)

    def _membership(self, obj):
        memberships = self.context.get('memberships') or {}
        return memberships.get(obj.pk)

    def get_my_role(self, obj):
        membership = self._membership(obj)
        return membership.role if membership else None

    def get_my_capabilities(self, obj):
        membership = self._membership(obj)
        return sorted(membership.capabilities) if membership else []

    def get_has_payout_account(self, obj):
        return bool(obj.paystack_subaccount_code)


class MembershipSerializer(serializers.ModelSerializer):
    user = UserSummarySerializer(read_only=True)
    invited_by = serializers.EmailField(source='invited_by.email', read_only=True, default=None)
    capabilities = serializers.SerializerMethodField()

    class Meta:
        model = Membership
        fields = ('id', 'user', 'role', 'custom_caps', 'capabilities', 'is_active', 'invited_by', 'created_at')
        read_only_fields = ('id', 'user', 'invited_by', 'created_at')

    def get_capabilities(self, obj):
        return sorted(obj.capabilities)

    def validate_role(self, value):
        if value not in ASSIGNABLE_ROLES:
            raise serializers.ValidationError('Ce role ne peut pas etre attribue.')
        return value

    def validate_custom_caps(self, value):
        errors = validate_caps_overrides(value)
        if errors:
            raise serializers.ValidationError(errors)
        return value


class InvitationSerializer(serializers.ModelSerializer):
    invited_by = serializers.EmailField(source='invited_by.email', read_only=True)
    capabilities = serializers.SerializerMethodField()

    class Meta:
        model = Invitation
        fields = ('id', 'email', 'role', 'custom_caps', 'capabilities', 'status', 'invited_by', 'expires_at', 'created_at')
        read_only_fields = ('id', 'status', 'invited_by', 'expires_at', 'created_at')

    def get_capabilities(self, obj):
        return sorted(resolve_capabilities(ROLE_DEFAULTS[obj.role], obj.custom_caps))

    def validate_email(self, value):
        return value.strip().lower()

    def validate_role(self, value):
        if value not in ASSIGNABLE_ROLES:
            raise serializers.ValidationError('Ce role ne peut pas etre attribue.')
        return value

    def validate_custom_caps(self, value):
        errors = validate_caps_overrides(value)
        if errors:
            raise serializers.ValidationError(errors)
        return value


class AcceptInvitationSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=128)


class AuditLogSerializer(serializers.ModelSerializer):
    actor = serializers.EmailField(source='actor.email', read_only=True, default=None)

    class Meta:
        model = AuditLog
        fields = ('id', 'action', 'actor', 'target_type', 'target_id', 'target_label', 'changes', 'created_at')
        read_only_fields = fields


class CapabilityCatalogSerializer(serializers.Serializer):
    capabilities = serializers.ListField(child=serializers.CharField())
    roles = serializers.DictField(child=serializers.ListField(child=serializers.CharField()))

    @staticmethod
    def build():
        return {
            'capabilities': sorted(CAPABILITIES),
            'roles': {role: sorted(caps) for role, caps in ROLE_DEFAULTS.items()},
        }


class OrganizationModerationSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Organization.STATUS_CHOICES, required=False)
    paystack_subaccount_code = serializers.RegexField(
        r'^ACCT_[A-Za-z0-9]{4,60}$', required=False, allow_blank=True,
        error_messages={'invalid': 'Code de sous-compte Paystack invalide (format ACCT_xxx).'},
    )
