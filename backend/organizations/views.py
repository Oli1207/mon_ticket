import secrets
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from config.slugs import unique_slug

from .audit import diff_fields, log_action
from .capabilities import ROLE_DEFAULTS, ROLE_OWNER, resolve_capabilities
from .models import AuditLog, Invitation, Membership, Organization, hash_token
from .permissions import HasOrgCapability, IsPlatformAdmin, OrgScopedMixin
from .serializers import (
    AcceptInvitationSerializer,
    AuditLogSerializer,
    CapabilityCatalogSerializer,
    InvitationSerializer,
    MembershipSerializer,
    OrganizationModerationSerializer,
    OrganizationSerializer,
    PublicOrganizationSerializer,
)
from .tasks import send_invitation_email

INVITATION_TTL = timedelta(days=7)
MAX_ORGS_PER_OWNER = 10
MAX_PENDING_INVITATIONS = 50


def ensure_grantable(actor_membership, target_caps):
    """Un non-proprietaire ne peut pas donner des droits qu'il n'a pas lui-meme."""
    if actor_membership.role == ROLE_OWNER:
        return
    excess = set(target_caps) - set(actor_membership.capabilities)
    if excess:
        raise PermissionDenied(
            "Tu ne peux pas accorder des droits que tu n'as pas toi-meme : " + ', '.join(sorted(excess)) + '.'
        )


class CapabilityCatalogView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        return Response(CapabilityCatalogSerializer.build())


class MyOrganizationsView(generics.ListCreateAPIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = OrganizationSerializer
    parser_classes = (JSONParser, MultiPartParser, FormParser)

    def get_queryset(self):
        return (
            Organization.objects.filter(memberships__user=self.request.user, memberships__is_active=True)
            .prefetch_related(
                Prefetch(
                    'memberships',
                    queryset=Membership.objects.filter(user=self.request.user, is_active=True),
                    to_attr='my_memberships',
                )
            )
            .order_by('-created_at')
        )

    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.get_queryset())
        memberships = {org.pk: org.my_memberships[0] for org in page if org.my_memberships}
        serializer = self.get_serializer(page, many=True, context={'request': request, 'memberships': memberships})
        return self.get_paginated_response(serializer.data)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if Organization.objects.filter(owner=request.user).count() >= MAX_ORGS_PER_OWNER:
            raise ValidationError({'detail': f'Tu ne peux pas creer plus de {MAX_ORGS_PER_OWNER} organisations.'})
        try:
            with transaction.atomic():
                org = serializer.save(
                    owner=request.user,
                    slug=unique_slug(Organization, serializer.validated_data['name']),
                    status=Organization.STATUS_PENDING,
                )
                membership = Membership.objects.create(organization=org, user=request.user, role=ROLE_OWNER)
                if not request.user.is_organizer:
                    request.user.is_organizer = True
                    request.user.save(update_fields=['is_organizer'])
                log_action(request, 'org.created', organization=org, target=org)
        except IntegrityError:
            raise ValidationError({'detail': 'Ce nom est deja pris, reessaie avec une variante.'})
        data = OrganizationSerializer(org, context={'request': request, 'memberships': {org.pk: membership}}).data
        return Response(data, status=status.HTTP_201_CREATED)


class OrganizationDetailView(OrgScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = (permissions.IsAuthenticated, HasOrgCapability)
    serializer_class = OrganizationSerializer
    capability_map = {'GET': None, 'PATCH': 'org.manage'}
    parser_classes = (JSONParser, MultiPartParser, FormParser)
    http_method_names = ('get', 'patch')

    def get_object(self):
        return self.get_organization()

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        org = self.get_organization()
        ctx['memberships'] = {org.pk: self.get_membership()}
        return ctx

    def perform_update(self, serializer):
        changes = diff_fields(serializer.instance, serializer.validated_data)
        org = serializer.save()
        if changes:
            log_action(self.request, 'org.updated', organization=org, target=org, changes=changes)


class MemberListView(OrgScopedMixin, generics.ListAPIView):
    permission_classes = (permissions.IsAuthenticated, HasOrgCapability)
    serializer_class = MembershipSerializer
    capability_map = {'GET': 'team.manage'}

    def get_queryset(self):
        return (
            Membership.objects.filter(organization=self.get_organization())
            .select_related('user', 'invited_by')
            .order_by('created_at')
        )


class MemberDetailView(OrgScopedMixin, generics.RetrieveUpdateDestroyAPIView):
    permission_classes = (permissions.IsAuthenticated, HasOrgCapability)
    serializer_class = MembershipSerializer
    capability_map = {'*': 'team.manage'}
    http_method_names = ('get', 'patch', 'delete')

    def get_queryset(self):
        return Membership.objects.filter(organization=self.get_organization()).select_related('user', 'invited_by')

    def _guard(self, membership):
        if membership.role == ROLE_OWNER:
            raise PermissionDenied('Le proprietaire ne peut pas etre modifie ni retire.')
        if membership.user_id == self.request.user.id:
            raise PermissionDenied('Tu ne peux pas modifier tes propres droits.')

    def perform_update(self, serializer):
        membership = serializer.instance
        self._guard(membership)
        role = serializer.validated_data.get('role', membership.role)
        custom_caps = serializer.validated_data.get('custom_caps', membership.custom_caps)
        ensure_grantable(self.get_membership(), resolve_capabilities(ROLE_DEFAULTS[role], custom_caps))
        changes = diff_fields(membership, serializer.validated_data)
        serializer.save()
        if changes:
            log_action(self.request, 'member.updated', organization=self.get_organization(), target=membership, changes=changes)

    def perform_destroy(self, membership):
        self._guard(membership)
        ensure_grantable(self.get_membership(), membership.capabilities)
        membership.is_active = False
        membership.save(update_fields=['is_active', 'updated_at'])
        log_action(self.request, 'member.removed', organization=self.get_organization(), target=membership)


class InvitationListCreateView(OrgScopedMixin, generics.ListCreateAPIView):
    permission_classes = (permissions.IsAuthenticated, HasOrgCapability)
    serializer_class = InvitationSerializer
    capability_map = {'*': 'team.manage'}

    def get_throttles(self):
        self.throttle_scope = 'invitation' if self.request.method == 'POST' else None
        return super().get_throttles()

    def get_queryset(self):
        return (
            Invitation.objects.filter(organization=self.get_organization())
            .select_related('invited_by')
            .order_by('-created_at')
        )

    def perform_create(self, serializer):
        org = self.get_organization()
        data = serializer.validated_data
        ensure_grantable(self.get_membership(), resolve_capabilities(ROLE_DEFAULTS[data['role']], data.get('custom_caps')))
        if Membership.objects.filter(organization=org, user__email=data['email'], is_active=True).exists():
            raise ValidationError({'email': ['Cette personne fait deja partie de ton equipe.']})
        pending = Invitation.objects.filter(organization=org, status=Invitation.STATUS_PENDING, expires_at__gt=timezone.now())
        if pending.filter(email=data['email']).exists():
            raise ValidationError({'email': ['Une invitation est deja en attente pour cet email.']})
        if pending.count() >= MAX_PENDING_INVITATIONS:
            raise ValidationError({'detail': 'Trop d\'invitations en attente. Revoque-en avant d\'en envoyer d\'autres.'})
        raw_token = secrets.token_urlsafe(32)
        invitation = serializer.save(
            organization=org,
            invited_by=self.request.user,
            token_hash=hash_token(raw_token),
            expires_at=timezone.now() + INVITATION_TTL,
        )
        log_action(self.request, 'invitation.created', organization=org, target=invitation,
                   changes={'email': invitation.email, 'role': invitation.role})
        transaction.on_commit(lambda: send_invitation_email.delay(invitation.pk, raw_token))


class InvitationRevokeView(OrgScopedMixin, APIView):
    permission_classes = (permissions.IsAuthenticated, HasOrgCapability)
    capability_map = {'POST': 'team.manage'}

    def post(self, request, org_slug, pk):
        org = self.get_organization()
        updated = Invitation.objects.filter(pk=pk, organization=org, status=Invitation.STATUS_PENDING).update(
            status=Invitation.STATUS_REVOKED
        )
        if not updated:
            raise ValidationError({'detail': 'Invitation introuvable ou deja traitee.'})
        invitation = Invitation.objects.get(pk=pk)
        log_action(request, 'invitation.revoked', organization=org, target=invitation)
        return Response(InvitationSerializer(invitation).data)


class AcceptInvitationView(APIView):
    permission_classes = (permissions.IsAuthenticated,)
    throttle_scope = 'invitation'

    def post(self, request):
        serializer = AcceptInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invalid = ValidationError({'detail': 'Invitation invalide ou expiree. Demande une nouvelle invitation.'})
        with transaction.atomic():
            invitation = (
                Invitation.objects.select_for_update(of=('self',))
                .select_related('organization')
                .filter(token_hash=hash_token(serializer.validated_data['token']))
                .first()
            )
            if (
                invitation is None
                or invitation.status != Invitation.STATUS_PENDING
                or invitation.expires_at <= timezone.now()
            ):
                raise invalid
            if invitation.email.lower() != request.user.email.lower():
                raise PermissionDenied(
                    "Cette invitation a ete envoyee a une autre adresse email. Connecte-toi avec le bon compte."
                )
            membership, created = Membership.objects.get_or_create(
                organization=invitation.organization,
                user=request.user,
                defaults={'role': invitation.role, 'custom_caps': invitation.custom_caps, 'invited_by': invitation.invited_by},
            )
            if not created:
                if membership.role == ROLE_OWNER:
                    raise invalid
                membership.role = invitation.role
                membership.custom_caps = invitation.custom_caps
                membership.is_active = True
                membership.invited_by = invitation.invited_by
                membership.save()
            invitation.status = Invitation.STATUS_ACCEPTED
            invitation.accepted_at = timezone.now()
            invitation.save(update_fields=['status', 'accepted_at'])
            if not request.user.is_organizer:
                request.user.is_organizer = True
                request.user.save(update_fields=['is_organizer'])
            log_action(request, 'invitation.accepted', organization=invitation.organization, target=membership)
        org = invitation.organization
        return Response(OrganizationSerializer(org, context={'request': request, 'memberships': {org.pk: membership}}).data)


class AuditLogListView(OrgScopedMixin, generics.ListAPIView):
    permission_classes = (permissions.IsAuthenticated, HasOrgCapability)
    serializer_class = AuditLogSerializer
    capability_map = {'GET': 'audit.view'}

    def get_queryset(self):
        return (
            AuditLog.objects.filter(organization=self.get_organization())
            .select_related('actor')
            .order_by('-created_at')
        )


class PublicOrganizationView(generics.RetrieveAPIView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = PublicOrganizationSerializer
    lookup_field = 'slug'
    queryset = Organization.objects.filter(status=Organization.STATUS_APPROVED)


class AdminOrganizationListView(generics.ListAPIView):
    permission_classes = (IsPlatformAdmin,)
    serializer_class = OrganizationSerializer
    throttle_scope = 'admin_sensitive'

    def get_queryset(self):
        qs = Organization.objects.select_related('owner').order_by('-created_at')
        status_filter = self.request.query_params.get('status')
        if status_filter in dict(Organization.STATUS_CHOICES):
            qs = qs.filter(status=status_filter)
        return qs


class AdminOrganizationModerationView(APIView):
    permission_classes = (IsPlatformAdmin,)
    throttle_scope = 'admin_sensitive'

    def patch(self, request, slug):
        serializer = OrganizationModerationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if not data:
            raise ValidationError({'detail': 'Aucune modification fournie.'})
        with transaction.atomic():
            org = get_object_or_404(Organization.objects.select_for_update(), slug=slug)
            changes = diff_fields(org, data)
            for field, value in data.items():
                setattr(org, field, value)
            if data.get('status') == Organization.STATUS_APPROVED and org.verified_at is None:
                org.verified_at = timezone.now()
            org.save()
            if changes:
                log_action(request, 'org.moderated', organization=org, target=org, changes=changes)
        return Response(OrganizationSerializer(org, context={'request': request}).data)
