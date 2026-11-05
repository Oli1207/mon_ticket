from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from .models import Membership, Organization


def get_active_membership(request, organization):
    """Membership active de l'utilisateur pour l'organisation, memoisee sur la requete."""
    cache = getattr(request, '_membership_cache', None)
    if cache is None:
        cache = request._membership_cache = {}
    if organization.pk not in cache:
        cache[organization.pk] = (
            Membership.objects.filter(organization=organization, user=request.user, is_active=True).first()
        )
    return cache[organization.pk]


class OrgScopedMixin:
    """
    Vue rattachee a une organisation via le kwarg d'URL `org_slug`.

    `capability_map` associe une methode HTTP a la capability requise.
    `None` signifie : etre membre actif suffit.
    """

    capability_map = {}
    org_lookup_kwarg = 'org_slug'

    def get_organization(self):
        if not hasattr(self, '_organization'):
            self._organization = get_object_or_404(
                Organization.objects.select_related('owner'), slug=self.kwargs[self.org_lookup_kwarg]
            )
        return self._organization

    def get_membership(self):
        return get_active_membership(self.request, self.get_organization())

    def required_capability(self):
        return self.capability_map.get(self.request.method, self.capability_map.get('*'))


class HasOrgCapability(BasePermission):
    message = "Tu n'as pas les droits necessaires pour cette action dans cette organisation."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        membership = view.get_membership()
        if membership is None:
            return False
        cap = view.required_capability()
        if cap is None:
            return True
        return cap in membership.capabilities


def require_capability(request, organization, cap):
    membership = get_active_membership(request, organization)
    if membership is None or cap not in membership.capabilities:
        raise PermissionDenied(HasOrgCapability.message)
    return membership


class IsPlatformAdmin(BasePermission):
    message = 'Acces reserve aux administrateurs de la plateforme.'

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)
