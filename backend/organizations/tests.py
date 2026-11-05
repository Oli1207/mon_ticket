from datetime import timedelta
from unittest import mock

from django.utils import timezone

from config.testing import APITestCase

from .capabilities import ROLE_FINANCE, ROLE_PRODUCER
from .models import AuditLog, Invitation, Membership, Organization, hash_token


class OrganizationPermissionTests(APITestCase):
    def setUp(self):
        super().setUp()
        self.org = self.make_org()
        self.owner = self.org.owner

    def test_anonymous_gets_401(self):
        self.assertEqual(self.client.get(f'/api/v1/organizer/orgs/{self.org.slug}/').status_code, 401)

    def test_non_member_gets_403(self):
        self.auth(self.make_user('stranger@example.com'))
        self.assertEqual(self.client.get(f'/api/v1/organizer/orgs/{self.org.slug}/').status_code, 403)

    def test_member_without_capability_gets_403(self):
        finance = self.make_user('finance@example.com')
        Membership.objects.create(organization=self.org, user=finance, role=ROLE_FINANCE)
        self.auth(finance)
        self.assertEqual(self.client.get(f'/api/v1/organizer/orgs/{self.org.slug}/').status_code, 200)
        self.assertEqual(self.client.get(f'/api/v1/organizer/orgs/{self.org.slug}/members/').status_code, 403)
        res = self.client.patch(f'/api/v1/organizer/orgs/{self.org.slug}/', {'bio': 'x'}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_custom_caps_override_role_defaults(self):
        finance = self.make_user('finance@example.com')
        Membership.objects.create(organization=self.org, user=finance, role=ROLE_FINANCE,
                                  custom_caps={'team.manage': True, 'finance.view': False})
        self.auth(finance)
        self.assertEqual(self.client.get(f'/api/v1/organizer/orgs/{self.org.slug}/members/').status_code, 200)

    def test_create_org_is_pending_and_owner_membership(self):
        user = self.make_user('new@example.com')
        self.auth(user)
        res = self.client.post('/api/v1/organizer/orgs/', {'name': 'Mon Collectif', 'kind': 'collective'}, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['status'], 'pending')
        self.assertEqual(res.data['my_role'], 'owner')
        user.refresh_from_db()
        self.assertTrue(user.is_organizer)
        self.assertTrue(AuditLog.objects.filter(action='org.created').exists())

    def test_member_cannot_grant_capabilities_he_lacks(self):
        producer = self.make_user('prod@example.com')
        Membership.objects.create(organization=self.org, user=producer, role=ROLE_PRODUCER, custom_caps={'team.manage': True})
        self.auth(producer)
        res = self.client.post(f'/api/v1/organizer/orgs/{self.org.slug}/invitations/',
                               {'email': 'x@example.com', 'role': 'admin'}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_owner_role_cannot_be_assigned(self):
        self.auth(self.owner)
        res = self.client.post(f'/api/v1/organizer/orgs/{self.org.slug}/invitations/',
                               {'email': 'x@example.com', 'role': 'owner'}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_unknown_capability_rejected(self):
        self.auth(self.owner)
        res = self.client.post(f'/api/v1/organizer/orgs/{self.org.slug}/invitations/',
                               {'email': 'x@example.com', 'role': 'finance', 'custom_caps': {'root.all': True}}, format='json')
        self.assertEqual(res.status_code, 400)

    @mock.patch('organizations.views.send_invitation_email.delay')
    def test_invitation_flow(self, _delay):
        self.auth(self.owner)
        res = self.client.post(f'/api/v1/organizer/orgs/{self.org.slug}/invitations/',
                               {'email': 'Guest@Example.com', 'role': 'door_manager'}, format='json')
        self.assertEqual(res.status_code, 201)
        self.assertNotIn('token', res.data)
        invitation = Invitation.objects.get()
        raw = 'raw-token-for-test'
        invitation.token_hash = hash_token(raw)
        invitation.save()

        self.auth(self.make_user('other@example.com'))
        self.assertEqual(self.client.post('/api/v1/organizer/invitations/accept/', {'token': raw}, format='json').status_code, 403)

        guest = self.make_user('guest@example.com')
        self.auth(guest)
        res = self.client.post('/api/v1/organizer/invitations/accept/', {'token': raw}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertIn('checkin', res.data['my_capabilities'])
        self.assertEqual(self.client.post('/api/v1/organizer/invitations/accept/', {'token': raw}, format='json').status_code, 400)

    def test_expired_invitation_rejected(self):
        guest = self.make_user('guest@example.com')
        Invitation.objects.create(organization=self.org, email='guest@example.com', role='finance',
                                  token_hash=hash_token('tok'), invited_by=self.owner,
                                  expires_at=timezone.now() - timedelta(minutes=1))
        self.auth(guest)
        self.assertEqual(self.client.post('/api/v1/organizer/invitations/accept/', {'token': 'tok'}, format='json').status_code, 400)

    def test_owner_cannot_be_removed(self):
        admin_user = self.make_user('admin@example.com')
        Membership.objects.create(organization=self.org, user=admin_user, role='admin')
        owner_membership = Membership.objects.get(user=self.owner)
        self.auth(admin_user)
        res = self.client.delete(f'/api/v1/organizer/orgs/{self.org.slug}/members/{owner_membership.pk}/')
        self.assertEqual(res.status_code, 403)

    def test_platform_admin_moderation(self):
        org = self.make_org(owner=self.make_user('o2@example.com'), status=Organization.STATUS_PENDING)
        self.auth(self.owner)
        self.assertEqual(self.client.patch(f'/api/v1/admin/organizations/{org.slug}/', {'status': 'approved'}, format='json').status_code, 403)
        self.auth(self.make_user('staff@example.com', is_staff=True))
        res = self.client.patch(f'/api/v1/admin/organizations/{org.slug}/', {'status': 'approved'}, format='json')
        self.assertEqual(res.status_code, 200)
        org.refresh_from_db()
        self.assertIsNotNone(org.verified_at)

    def test_audit_log_is_immutable(self):
        log = AuditLog.objects.create(action='x')
        log.action = 'y'
        with self.assertRaises(Exception):
            log.save()
