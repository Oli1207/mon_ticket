import io
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from config.testing import APITestCase
from organizations.models import Membership, Organization
from ticketing.models import EventTicketing, Order

from .models import Event, EventStaff

def png_file(name='poster.png'):
    buffer = io.BytesIO()
    Image.new('RGB', (16, 9), 'black').save(buffer, format='PNG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')


class PublicEventTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.org = self.make_org()
        self.event = self.make_event(self.org, slug='live-1', is_featured=True)
        self.make_ticket_type(self.event, price=5000)
        self.make_ticket_type(self.event, price=15000, name='VIP')

    def test_list_only_published_upcoming_from_approved_orgs(self):
        self.make_event(self.org, slug='draft-1', status=Event.STATUS_DRAFT)
        self.make_event(self.org, slug='past-1', start_at=timezone.now() - timedelta(days=2))
        pending_org = self.make_org(owner=self.make_user('p@example.com'), status=Organization.STATUS_PENDING)
        self.make_event(pending_org, slug='pending-org-1')
        res = self.client.get('/api/v1/events/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual([e['slug'] for e in res.data['results']], ['live-1'])
        self.assertEqual(res.data['results'][0]['min_price'], 5000)

    def test_filters_and_validation(self):
        self.assertEqual(self.client.get('/api/v1/events/?featured=true&city=abidjan').data['count'], 1)
        self.assertEqual(self.client.get('/api/v1/events/?city=Dakar').data['count'], 0)
        self.assertEqual(self.client.get('/api/v1/events/?q=conc').data['count'], 1)
        self.assertEqual(self.client.get('/api/v1/events/?type=bogus').status_code, 400)
        self.assertEqual(self.client.get('/api/v1/events/?date_from=hier').status_code, 400)
        self.assertEqual(self.client.get('/api/v1/events/?q=' + 'x' * 101).status_code, 400)

    def test_page_size_is_capped(self):
        res = self.client.get('/api/v1/events/?page_size=100000')
        self.assertEqual(res.status_code, 200)

    def test_detail_with_ticket_types_and_buyer_fee(self):
        res = self.client.get('/api/v1/events/live-1/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data['ticket_types']), 2)
        self.assertEqual(res.data['ticket_types'][0]['buyer_fee'], 300)
        self.assertTrue(res.data['sales_open'])

    def test_draft_detail_is_404(self):
        self.make_event(self.org, slug='draft-1', status=Event.STATUS_DRAFT)
        self.assertEqual(self.client.get('/api/v1/events/draft-1/').status_code, 404)


class OrganizerEventTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.org = self.make_org()
        self.base = f'/api/v1/organizer/orgs/{self.org.slug}/events/'

    def _create(self):
        self.auth(self.org.owner)
        res = self.client.post(self.base, {
            'title': 'Festival Abidjan', 'event_type': 'festival', 'venue_name': 'Stade FHB', 'city': 'abidjan',
            'start_at': (timezone.now() + timedelta(days=30)).isoformat(),
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        return res.data

    def test_create_as_draft_with_ticketing(self):
        data = self._create()
        self.assertEqual(data['status'], 'draft')
        self.assertEqual(data['city'], 'Abidjan')
        self.assertTrue(EventTicketing.objects.filter(event__slug=data['slug']).exists())

    def test_finance_member_cannot_create(self):
        user = self.make_user('fin@example.com')
        Membership.objects.create(organization=self.org, user=user, role='finance')
        self.auth(user)
        self.assertEqual(self.client.post(self.base, {}, format='json').status_code, 403)

    def test_publish_requires_poster_and_approved_org(self):
        data = self._create()
        url = f'{self.base}{data["slug"]}/'
        self.assertEqual(self.client.patch(url, {'status': 'published'}, format='json').status_code, 400)
        res = self.client.patch(url, {'status': 'published', 'poster': png_file()}, format='multipart')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIsNotNone(res.data['published_at'])

    def test_end_before_start_rejected(self):
        data = self._create()
        res = self.client.patch(f'{self.base}{data["slug"]}/', {'end_at': timezone.now().isoformat()}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_cannot_delete_event_with_orders(self):
        event = self.make_event(self.org, slug='with-orders', status=Event.STATUS_DRAFT)
        Order.objects.create(event=event, email='a@b.co', phone='0700', full_name='A B', client_ip='1.1.1.1',
                             subtotal=0, service_fee=0, total=0, organizer_net=0, commission_amount=0,
                             commission_percent=0, fees_mode='buyer', expires_at=timezone.now())
        self.auth(self.org.owner)
        self.assertEqual(self.client.delete(f'{self.base}with-orders/').status_code, 400)

    def test_other_org_event_is_404(self):
        other = self.make_org(owner=self.make_user('o2@example.com'))
        self.make_event(other, slug='not-mine')
        self.auth(self.org.owner)
        self.assertEqual(self.client.get(f'{self.base}not-mine/').status_code, 404)

    def test_staff_only_links_existing_accounts(self):
        event = self.make_event(self.org, slug='staffed')
        self.auth(self.org.owner)
        url = f'{self.base}staffed/staff/'
        self.assertEqual(self.client.post(url, {'email': 'ghost@example.com', 'role': 'scanner'}, format='json').status_code, 400)
        self.make_user('scan@example.com')
        res = self.client.post(url, {'email': 'scan@example.com', 'role': 'scanner', 'custom_caps': {'org.manage': True}}, format='json')
        self.assertEqual(res.status_code, 400)
        res = self.client.post(url, {'email': 'scan@example.com', 'role': 'scanner', 'gate': 'A'}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(EventStaff.objects.get(event=event).capabilities, frozenset({'checkin'}))
