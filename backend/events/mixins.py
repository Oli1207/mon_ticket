from django.shortcuts import get_object_or_404

from organizations.permissions import OrgScopedMixin

from .models import Event


class OrgEventMixin(OrgScopedMixin):
    """Vue rattachee a un evenement d'une organisation : orgs/<org_slug>/events/<event_slug>/..."""

    event_lookup_kwarg = 'event_slug'

    def get_event(self):
        if not hasattr(self, '_event'):
            self._event = get_object_or_404(
                Event.objects.select_related('organization', 'ticketing', 'ticketing__partner_code'),
                organization=self.get_organization(),
                slug=self.kwargs[self.event_lookup_kwarg],
            )
        return self._event
