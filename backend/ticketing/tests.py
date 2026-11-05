import hashlib
import hmac
import json
import threading
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase
from django.utils import timezone

from rest_framework.test import APIClient

from config.testing import APITestCase, TransactionAPITestCase
from events.models import EventStaff

from .models import EventTicketing, Order, PartnerCode, PricingChange, PricingSettings, PromoCode, ScanLog, Ticket, TicketType
from .pricing import compute_order_amounts, resolve_commission_percent
from .tasks import expire_pending_orders

WEBHOOK_SECRET = 'sk_test_dummy'


class CommissionResolutionTests(SimpleTestCase):
    def setUp(self):
        self.pricing = PricingSettings(commission_percent=Decimal('6.00'))
        self.partner = PartnerCode(code='P', commission_percent=Decimal('4.00'), is_active=True)
        self.ticketing = EventTicketing(fees_mode='buyer')
        self.tt = TicketType(price=Decimal('10000'), quota=10)

    def test_most_specific_non_null_wins(self):
        now = timezone.now()
        self.assertEqual(resolve_commission_percent(self.pricing, self.ticketing, self.tt, now), Decimal('6.00'))
        self.ticketing.partner_code = self.partner
        self.assertEqual(resolve_commission_percent(self.pricing, self.ticketing, self.tt, now), Decimal('4.00'))
        self.ticketing.commission_override = Decimal('3.00')
        self.assertEqual(resolve_commission_percent(self.pricing, self.ticketing, self.tt, now), Decimal('3.00'))
        self.tt.commission_override = Decimal('0.00')
        self.assertEqual(resolve_commission_percent(self.pricing, self.ticketing, self.tt, now), Decimal('0.00'))

    def test_expired_partner_code_falls_back_to_global(self):
        self.partner.expires_at = timezone.now() - timedelta(days=1)
        self.ticketing.partner_code = self.partner
        self.assertEqual(resolve_commission_percent(self.pricing, self.ticketing, self.tt, timezone.now()), Decimal('6.00'))

    def test_amounts_buyer_and_organizer_modes(self):
        lines = [(Decimal('10000'), 2, Decimal('6'))]
        buyer = compute_order_amounts(lines, 'buyer')
        self.assertEqual((buyer['total'], buyer['service_fee'], buyer['organizer_net']), (Decimal('21200'), Decimal('1200'), Decimal('20000')))
        org = compute_order_amounts(lines, 'organizer')
        self.assertEqual((org['total'], org['service_fee'], org['organizer_net']), (Decimal('20000'), Decimal('0'), Decimal('18800')))

    def test_commission_applies_after_discount(self):
        promo = PromoCode(kind='percent', value=Decimal('50'))
        amounts = compute_order_amounts([(Decimal('10000'), 1, Decimal('6'))], 'buyer', promo)
        self.assertEqual(amounts['discount'], Decimal('5000'))
        self.assertEqual(amounts['commission_amount'], Decimal('300'))
        self.assertEqual(amounts['total'], Decimal('5300'))


class OrderFlowTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.org = self.make_org()
        self.event = self.make_event(self.org)
        self.tt = self.make_ticket_type(self.event, price=10000, quota=5)

    def order(self, email='buyer@example.com', quantity=1, ip='10.0.0.1', **extra):
        payload = {
            'event': self.event.slug, 'items': [{'ticket_type_id': self.tt.pk, 'quantity': quantity}],
            'email': email, 'phone': '+225 07 00 00 00 00', 'full_name': 'Awa Kone', **extra,
        }
        return self.client.post('/api/v1/orders/', payload, format='json', REMOTE_ADDR=ip)

    def test_create_order_reserves_stock(self):
        res = self.order(quantity=2)
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['total'], 21200)
        self.assertEqual(res.data['status'], 'pending')
        self.assertTrue(res.data['access_token'])
        self.tt.refresh_from_db()
        self.assertEqual((self.tt.reserved_count, self.tt.sold_count), (2, 0))

    def test_cannot_oversell(self):
        self.assertEqual(self.order('a@example.com', 4).status_code, 201)
        res = self.order('b@example.com', 2)
        self.assertEqual(res.status_code, 409)
        self.tt.refresh_from_db()
        self.assertEqual(self.tt.reserved_count, 4)

    def test_database_constraint_blocks_oversell(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            TicketType.objects.filter(pk=self.tt.pk).update(sold_count=6)

    def test_anti_hoarding_per_email(self):
        self.assertEqual(self.order(quantity=1).status_code, 201)
        self.assertEqual(self.order(quantity=1).status_code, 201)
        self.assertEqual(self.order(quantity=1).status_code, 429)

    def test_anti_hoarding_per_ip(self):
        settings_obj = PricingSettings.load()
        settings_obj.max_pending_per_ip = 1
        settings_obj.save()
        self.assertEqual(self.order('a@example.com').status_code, 201)
        self.assertEqual(self.order('b@example.com').status_code, 429)

    def test_validation_errors(self):
        self.assertEqual(self.order(quantity=0).status_code, 400)
        res = self.client.post('/api/v1/orders/', {
            'event': self.event.slug, 'items': [], 'email': 'x@example.com', 'phone': '070000', 'full_name': 'A B',
        }, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.order(phone='pas-un-numero').status_code, 400)
        self.assertEqual(self.order(quantity=11).status_code, 400)

    def test_sales_closed(self):
        EventTicketing.objects.filter(event=self.event).update(sales_open=False)
        self.assertEqual(self.order().status_code, 400)

    def test_expired_promo_code(self):
        PromoCode.objects.create(event=self.event, code='OLD', kind='percent', value=10,
                                 expires_at=timezone.now() - timedelta(hours=1))
        res = self.order(promo_code='old')
        self.assertEqual(res.status_code, 400)
        self.assertIn('promo_code', res.data)

    def test_promo_code_max_uses(self):
        promo = PromoCode.objects.create(event=self.event, code='ONE', kind='amount', value=1000, max_uses=1)
        res = self.order('a@example.com', promo_code='one')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['discount'], 1000)
        self.assertEqual(self.order('b@example.com', promo_code='ONE').status_code, 400)
        promo.refresh_from_db()
        self.assertEqual(promo.uses, 1)

    def test_free_order_is_paid_immediately_with_tickets(self):
        self.tt.price = 0
        self.tt.save()
        res = self.order(quantity=2)
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['status'], 'paid')
        self.assertEqual(len(res.data['tickets']), 2)
        self.assertTrue(res.data['tickets'][0]['qr_payload'].startswith('SPT1.'))
        self.tt.refresh_from_db()
        self.assertEqual((self.tt.reserved_count, self.tt.sold_count), (0, 2))

    def test_order_access_requires_token(self):
        res = self.order()
        public_id, token = res.data['public_id'], res.data['access_token']
        self.assertEqual(self.client.get(f'/api/v1/orders/{public_id}/').status_code, 404)
        self.assertEqual(self.client.get(f'/api/v1/orders/{public_id}/', HTTP_X_ORDER_TOKEN='wrong').status_code, 404)
        res = self.client.get(f'/api/v1/orders/{public_id}/', HTTP_X_ORDER_TOKEN=token)
        self.assertEqual(res.status_code, 200)
        self.assertNotIn('access_token', res.data)

    def test_cancel_releases_stock(self):
        res = self.order(quantity=3)
        r = self.client.post(f'/api/v1/orders/{res.data["public_id"]}/cancel/', HTTP_X_ORDER_TOKEN=res.data['access_token'])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['status'], 'cancelled')
        self.tt.refresh_from_db()
        self.assertEqual(self.tt.reserved_count, 0)

    @mock.patch('ticketing.paystack.requests.request')
    def test_pay_initializes_split_transaction(self, request_mock):
        request_mock.return_value = mock.Mock(status_code=200, json=lambda: {
            'status': True, 'data': {'authorization_url': 'https://checkout.paystack.com/x', 'access_code': 'x'},
        })
        res = self.order(quantity=2)
        r = self.client.post(f'/api/v1/orders/{res.data["public_id"]}/pay/', HTTP_X_ORDER_TOKEN=res.data['access_token'])
        self.assertEqual(r.status_code, 200)
        body = request_mock.call_args.kwargs['json']
        self.assertEqual(body['amount'], 2120000)
        self.assertEqual(body['transaction_charge'], 120000)
        self.assertEqual(body['subaccount'], 'ACCT_test123')
        self.assertTrue(Order.objects.get(public_id=res.data['public_id']).paystack_reference.startswith(res.data['public_id']))

    def test_expiry_task_releases_stock_idempotently(self):
        res = self.order(quantity=2)
        Order.objects.filter(public_id=res.data['public_id']).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(expire_pending_orders(), 1)
        self.assertEqual(expire_pending_orders(), 0)
        self.tt.refresh_from_db()
        self.assertEqual(self.tt.reserved_count, 0)
        self.assertEqual(Order.objects.get().status, Order.STATUS_EXPIRED)


class WebhookTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.org = self.make_org()
        self.event = self.make_event(self.org)
        self.tt = self.make_ticket_type(self.event, price=10000, quota=2)
        res = self.client.post('/api/v1/orders/', {
            'event': self.event.slug, 'items': [{'ticket_type_id': self.tt.pk, 'quantity': 2}],
            'email': 'buyer@example.com', 'phone': '0700000000', 'full_name': 'Awa Kone',
        }, format='json')
        self.order = Order.objects.get(public_id=res.data['public_id'])
        self.reference = f'{self.order.public_id}-abcd1234'
        Order.objects.filter(pk=self.order.pk).update(paystack_reference=self.reference)

    def send(self, amount=None, secret=WEBHOOK_SECRET, event='charge.success', reference=None):
        body = json.dumps({'event': event, 'data': {
            'reference': reference or self.reference, 'amount': amount if amount is not None else 2120000,
            'currency': 'XOF', 'metadata': {'order_public_id': self.order.public_id},
        }}).encode()
        signature = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
        return self.client.post('/api/v1/payments/paystack/webhook/', body, content_type='application/json',
                                HTTP_X_PAYSTACK_SIGNATURE=signature)

    def test_bad_signature_rejected(self):
        self.assertEqual(self.send(secret='forged').status_code, 401)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.STATUS_PENDING)

    def test_success_marks_paid_and_issues_tickets_once(self):
        self.assertEqual(self.send().status_code, 200)
        self.assertEqual(self.send().status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.STATUS_PAID)
        self.assertEqual(Ticket.objects.filter(order=self.order).count(), 2)
        self.tt.refresh_from_db()
        self.assertEqual((self.tt.reserved_count, self.tt.sold_count), (0, 2))

    def test_wrong_amount_needs_refund(self):
        self.send(amount=100)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.STATUS_NEEDS_REFUND)
        self.assertFalse(Ticket.objects.exists())
        self.tt.refresh_from_db()
        self.assertEqual(self.tt.reserved_count, 0)

    def test_foreign_reference_ignored(self):
        self.send(reference='OTHERORDER-1234')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.STATUS_PENDING)

    def test_payment_after_expiry_reacquires_stock_if_available(self):
        Order.objects.filter(pk=self.order.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        expire_pending_orders()
        self.send()
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.STATUS_PAID)
        self.tt.refresh_from_db()
        self.assertEqual((self.tt.reserved_count, self.tt.sold_count), (0, 2))

    def test_payment_after_expiry_without_stock_needs_refund(self):
        Order.objects.filter(pk=self.order.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        expire_pending_orders()
        TicketType.objects.filter(pk=self.tt.pk).update(sold_count=2)
        self.send()
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.STATUS_NEEDS_REFUND)


class ScanTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.org = self.make_org()
        self.event = self.make_event(self.org)
        self.tt = self.make_ticket_type(self.event, price=0, quota=10)
        self.vip = self.make_ticket_type(self.event, price=0, quota=10, name='VIP')
        res = self.client.post('/api/v1/orders/', {
            'event': self.event.slug, 'items': [{'ticket_type_id': self.tt.pk, 'quantity': 1}],
            'email': 'buyer@example.com', 'phone': '0700000000', 'full_name': 'Awa Kone',
        }, format='json')
        self.ticket = Ticket.objects.get(order__public_id=res.data['public_id'])
        self.url = f'/api/v1/organizer/events/{self.event.slug}/scan/'

    def scan(self, payload, gate='A'):
        return self.client.post(self.url, {'payload': payload, 'gate': gate}, format='json')

    def test_anonymous_401_and_stranger_403(self):
        self.assertEqual(self.scan(self.ticket.qr_payload).status_code, 401)
        self.auth(self.make_user('stranger@example.com'))
        self.assertEqual(self.scan(self.ticket.qr_payload).status_code, 403)

    def test_accept_then_already_used(self):
        self.auth(self.org.owner)
        res = self.scan(self.ticket.qr_payload)
        self.assertEqual(res.data['result'], 'accepted')
        res = self.scan(self.ticket.qr_payload)
        self.assertEqual(res.data['result'], 'already_used')
        self.assertEqual(ScanLog.objects.filter(event=self.event).count(), 2)

    def test_forged_signature_is_invalid(self):
        self.auth(self.org.owner)
        forged = f'SPT1.{self.ticket.public_id}.000000000000'
        self.assertEqual(self.scan(forged).data['result'], 'invalid')
        self.assertEqual(self.scan('garbage').data['result'], 'invalid')
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, Ticket.STATUS_VALID)

    def test_wrong_event(self):
        other = self.make_event(self.org, slug='other-event')
        self.auth(self.org.owner)
        res = self.client.post(f'/api/v1/organizer/events/{other.slug}/scan/', {'payload': self.ticket.qr_payload}, format='json')
        self.assertEqual(res.data['result'], 'wrong_event')

    def test_staff_restricted_to_ticket_types(self):
        scanner = self.make_user('scanner@example.com')
        staff = EventStaff.objects.create(event=self.event, user=scanner, email=scanner.email, role='scanner')
        staff.ticket_types.set([self.vip])
        self.auth(scanner)
        self.assertEqual(self.scan(self.ticket.qr_payload).data['result'], 'not_allowed')
        staff.ticket_types.set([self.tt])
        self.assertEqual(self.scan(self.ticket.qr_payload).data['result'], 'accepted')

    def test_cancelled_ticket(self):
        Ticket.objects.filter(pk=self.ticket.pk).update(status=Ticket.STATUS_CANCELLED)
        self.auth(self.org.owner)
        self.assertEqual(self.scan(self.ticket.qr_payload).data['result'], 'cancelled')


class OrganizerTicketingTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.org = self.make_org()
        self.event = self.make_event(self.org, sales_open=False)
        self.base = f'/api/v1/organizer/orgs/{self.org.slug}/events/{self.event.slug}/'
        self.auth(self.org.owner)

    def test_ticket_type_crud_and_commission_is_read_only(self):
        res = self.client.post(self.base + 'ticket-types/', {
            'name': 'Early', 'price': 5000, 'quota': 100, 'commission_override': '0.00',
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertIsNone(res.data['commission_override'])
        self.assertEqual(res.data['effective_commission_percent'], '6.00')

    def test_quota_cannot_drop_below_sold(self):
        tt = self.make_ticket_type(self.event, quota=10, sold_count=5)
        res = self.client.patch(f'{self.base}ticket-types/{tt.pk}/', {'quota': 4}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_open_sales_requires_ticket_types(self):
        self.assertEqual(self.client.patch(self.base + 'ticketing/', {'sales_open': True}, format='json').status_code, 400)
        self.make_ticket_type(self.event)
        self.assertEqual(self.client.patch(self.base + 'ticketing/', {'sales_open': True}, format='json').status_code, 200)

    def test_partner_code_is_logged(self):
        PartnerCode.objects.create(code='AFRIK', commission_percent=Decimal('4.00'), created_by=self.org.owner)
        res = self.client.patch(self.base + 'ticketing/', {'partner_code': 'afrik'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['effective_commission_percent'], '4.00')
        self.assertTrue(PricingChange.objects.filter(field='partner_code', new_value='AFRIK').exists())
        self.assertEqual(self.client.patch(self.base + 'ticketing/', {'partner_code': 'NOPE'}, format='json').status_code, 400)

    def test_stats_requires_sales_view(self):
        self.assertEqual(self.client.get(self.base + 'stats/').status_code, 200)
        producer = self.make_user('prod@example.com')
        self.org.memberships.create(user=producer, role='producer')
        self.auth(producer)
        self.assertEqual(self.client.get(self.base + 'stats/').status_code, 403)

    def test_attendee_email_hidden_without_contact_cap(self):
        from organizations.models import Membership
        door = self.make_user('door@example.com')
        Membership.objects.create(organization=self.org, user=door, role='door_manager')
        tt = self.make_ticket_type(self.event, price=0)
        order = Order.objects.create(event=self.event, email='a@b.co', phone='0700', full_name='A B', client_ip='1.1.1.1',
                                     subtotal=0, service_fee=0, total=0, organizer_net=0, commission_amount=0,
                                     commission_percent=0, fees_mode='buyer', expires_at=timezone.now(), status='paid')
        Ticket.objects.create(order=order, event=self.event, ticket_type=tt, holder_name='A B', holder_email='a@b.co')
        self.auth(door)
        res = self.client.get(self.base + 'attendees/')
        self.assertEqual(res.status_code, 200)
        self.assertNotIn('holder_email', res.data['results'][0])


class AdminPricingTests(APITestCase):
    def test_pricing_update_is_logged_and_restricted(self):
        user = self.make_user()
        self.auth(user)
        self.assertEqual(self.client.patch('/api/v1/admin/pricing/', {'commission_percent': '5'}, format='json').status_code, 403)
        admin = self.make_user('admin@example.com', is_staff=True)
        self.auth(admin)
        res = self.client.patch('/api/v1/admin/pricing/', {'commission_percent': '5.00'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(PricingSettings.load().commission_percent, Decimal('5.00'))
        change = PricingChange.objects.get()
        self.assertEqual((change.target, change.field, change.old_value, change.new_value), ('global', 'commission_percent', '6.00', '5.00'))
        self.assertEqual(self.client.patch('/api/v1/admin/pricing/', {'commission_percent': '150'}, format='json').status_code, 400)


class ConcurrencyTests(TransactionAPITestCase):
    """Vrais acces paralleles a PostgreSQL : le verrou sur TicketType doit empecher toute survente."""

    def test_parallel_orders_never_oversell(self):
        org = self.make_org()
        event = self.make_event(org)
        tt = self.make_ticket_type(event, price=10000, quota=5)
        results = []

        def buy(i):
            client = APIClient()
            res = client.post('/api/v1/orders/', {
                'event': event.slug, 'items': [{'ticket_type_id': tt.pk, 'quantity': 1}],
                'email': f'buyer{i}@example.com', 'phone': '0700000000', 'full_name': 'Awa Kone',
            }, format='json', REMOTE_ADDR=f'10.0.0.{i}')
            results.append(res.status_code)
            connection.close()

        threads = [threading.Thread(target=buy, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        tt.refresh_from_db()
        self.assertEqual(results.count(201), 5)
        self.assertEqual(results.count(409), 15)
        self.assertEqual(tt.reserved_count, 5)
        self.assertEqual(Order.objects.count(), 5)


class QueryCountTests(APITestCase):
    def test_public_list_and_order_list_have_constant_queries(self):
        org = self.make_org()
        for i in range(5):
            event = self.make_event(org, slug=f'event-{i}')
            self.make_ticket_type(event)
        with self.assertNumQueries(2):
            self.client.get('/api/v1/events/')

        event = self.make_event(org, slug='sales')
        tt = self.make_ticket_type(event, price=0, quota=50)
        for i in range(5):
            self.client.post('/api/v1/orders/', {
                'event': event.slug, 'items': [{'ticket_type_id': tt.pk, 'quantity': 1}],
                'email': f'b{i}@example.com', 'phone': '0700000000', 'full_name': 'Awa Kone',
            }, format='json', REMOTE_ADDR=f'10.0.1.{i}')
        self.auth(org.owner)
        url = f'/api/v1/organizer/orgs/{org.slug}/events/sales/orders/'
        with self.assertNumQueries(7):
            res = self.client.get(url)
        self.assertEqual(res.data['count'], 5)
