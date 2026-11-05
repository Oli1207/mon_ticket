import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from events.models import Event
from organizations.capabilities import ROLE_OWNER
from organizations.models import Membership, Organization
from ticketing.models import EventTicketing, PricingSettings, TicketType

User = get_user_model()

TEST_CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


TEST_SETTINGS = dict(
    CACHES=TEST_CACHES,
    MEDIA_ROOT=tempfile.mkdtemp(prefix='mon_ticket_test_media_'),
    PAYSTACK_SECRET_KEY='sk_test_dummy',
    PAYSTACK_WEBHOOK_SECRET='sk_test_dummy',
    PAYSTACK_CURRENCY='XOF',
    QR_SECRET_KEY='qr-test-key',
)


class APITestMixin:
    """Cache local vide (throttles remis a zero) et fabriques de donnees."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def make_user(self, email='user@example.com', verified=True, **kwargs):
        if verified:
            kwargs.setdefault('email_verified_at', timezone.now())
        return User.objects.create_user(email=email, password='Str0ng-pass!42', first_name='Awa', last_name='Kone', **kwargs)

    def auth(self, user):
        self.client.force_authenticate(user=user)

    def make_org(self, owner=None, status=Organization.STATUS_APPROVED, subaccount='ACCT_test123'):
        owner = owner or self.make_user('owner@example.com')
        org = Organization.objects.create(
            name='Live Nation Abidjan', slug=f'org-{Organization.objects.count() + 1}', kind='promoter',
            status=status, owner=owner, paystack_subaccount_code=subaccount,
        )
        Membership.objects.create(organization=org, user=owner, role=ROLE_OWNER)
        return org

    def make_event(self, org, slug='concert-1', sales_open=True, **kwargs):
        defaults = dict(
            title='Concert Test', slug=slug, start_at=timezone.now() + timedelta(days=10),
            venue_name='Palais de la Culture', city='Abidjan', status=Event.STATUS_PUBLISHED,
        )
        defaults.update(kwargs)
        event = Event.objects.create(organization=org, **defaults)
        EventTicketing.objects.create(event=event, sales_open=sales_open)
        return event

    def make_ticket_type(self, event, price=10000, quota=100, **kwargs):
        return TicketType.objects.create(event=event, name=kwargs.pop('name', 'Standard'), price=price, quota=quota, **kwargs)

    def pricing(self):
        return PricingSettings.load()


@override_settings(**TEST_SETTINGS)
class APITestCase(APITestMixin, TestCase):
    pass


@override_settings(**TEST_SETTINGS)
class TransactionAPITestCase(APITestMixin, TransactionTestCase):
    pass
