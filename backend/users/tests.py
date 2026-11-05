from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.utils import timezone

from config.testing import APITestCase
from config.tokens import hash_token

from .models import EmailVerificationToken

User = get_user_model()


class AuthTests(APITestCase):
    def test_register_then_login(self):
        res = self.client.post('/api/v1/auth/register/', {
            'email': 'Nouveau@Example.com', 'password': 'Tr3s-solide-42', 'first_name': 'Koffi', 'last_name': 'Yao',
        }, format='json')
        self.assertEqual(res.status_code, 201)
        res = self.client.post('/api/v1/auth/login/', {'email': 'nouveau@example.com', 'password': 'Tr3s-solide-42'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertIn('access', res.data)
        self.assertEqual(res.data['user']['email'], 'nouveau@example.com')

    def test_register_existing_email_gives_same_response(self):
        self.make_user('pris@example.com')
        res = self.client.post('/api/v1/auth/register/', {
            'email': 'pris@example.com', 'password': 'Tr3s-solide-42', 'first_name': 'A', 'last_name': 'B',
        }, format='json')
        self.assertEqual(res.status_code, 201)

    def test_weak_password_rejected(self):
        res = self.client.post('/api/v1/auth/register/', {
            'email': 'x@example.com', 'password': '12345678', 'first_name': 'A', 'last_name': 'B',
        }, format='json')
        self.assertEqual(res.status_code, 400)

    def test_bad_login_is_generic(self):
        res = self.client.post('/api/v1/auth/login/', {'email': 'nobody@example.com', 'password': 'x'}, format='json')
        self.assertEqual(res.status_code, 401)

    def test_profile_requires_auth(self):
        self.assertEqual(self.client.get('/api/v1/auth/me/').status_code, 401)
        user = self.make_user()
        self.auth(user)
        res = self.client.patch('/api/v1/auth/me/', {'city': 'Dakar', 'is_staff': True}, format='json')
        self.assertEqual(res.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.city, 'Dakar')
        self.assertFalse(user.is_staff)


class EmailVerificationTests(APITestCase):
    def register(self, email='nouveau@example.com'):
        with mock.patch('users.verification.send_verification_email.delay') as delay, \
                self.captureOnCommitCallbacks(execute=True):
            res = self.client.post('/api/v1/auth/register/', {
                'email': email, 'password': 'Tr3s-solide-42', 'first_name': 'Koffi', 'last_name': 'Yao',
            }, format='json')
        self.assertEqual(res.status_code, 201)
        return delay

    def test_registration_sends_hashed_single_use_token(self):
        delay = self.register()
        user = User.objects.get(email='nouveau@example.com')
        self.assertFalse(user.is_email_verified)
        delay.assert_called_once()
        raw_token = delay.call_args.args[1]
        stored = EmailVerificationToken.objects.get(user=user)
        self.assertNotEqual(stored.token_hash, raw_token)
        self.assertEqual(stored.token_hash, hash_token(raw_token))

        res = self.client.post('/api/v1/auth/verify-email/', {'token': raw_token}, format='json')
        self.assertEqual(res.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.is_email_verified)
        res = self.client.post('/api/v1/auth/verify-email/', {'token': raw_token}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_existing_email_registration_sends_nothing(self):
        self.make_user('pris@example.com')
        delay = self.register('pris@example.com')
        delay.assert_not_called()

    def test_expired_or_unknown_token_rejected(self):
        delay = self.register()
        raw_token = delay.call_args.args[1]
        EmailVerificationToken.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.client.post('/api/v1/auth/verify-email/', {'token': raw_token}, format='json').status_code, 400)
        self.assertEqual(self.client.post('/api/v1/auth/verify-email/', {'token': 'nope'}, format='json').status_code, 400)
        self.assertFalse(User.objects.get(email='nouveau@example.com').is_email_verified)

    def test_resend_is_generic_and_rate_limited_per_user(self):
        self.make_user('deja@example.com')
        self.make_user('attente@example.com', verified=False)
        with mock.patch('users.verification.send_verification_email.delay') as delay, \
                self.captureOnCommitCallbacks(execute=True):
            responses = [
                self.client.post('/api/v1/auth/verify-email/resend/', {'email': email}, format='json')
                for email in ('inconnu@example.com', 'deja@example.com', 'Attente@Example.com', 'attente@example.com')
            ]
        self.assertEqual({r.status_code for r in responses}, {200})
        self.assertEqual(len({r.data['detail'] for r in responses}), 1)
        self.assertEqual(delay.call_count, 1)

    def test_resend_replaces_previous_token(self):
        user = self.make_user('attente@example.com', verified=False)
        EmailVerificationToken.objects.create(user=user, token_hash=hash_token('ancien'),
                                              expires_at=timezone.now() + timedelta(hours=1))
        EmailVerificationToken.objects.update(created_at=timezone.now() - timedelta(minutes=5))
        with mock.patch('users.verification.send_verification_email.delay'), self.captureOnCommitCallbacks(execute=True):
            self.client.post('/api/v1/auth/verify-email/resend/', {'email': user.email}, format='json')
        self.assertEqual(self.client.post('/api/v1/auth/verify-email/', {'token': 'ancien'}, format='json').status_code, 400)

    def test_profile_exposes_status_read_only(self):
        user = self.make_user(verified=False)
        self.auth(user)
        res = self.client.patch('/api/v1/auth/me/', {'email_verified': True}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.data['email_verified'])

    def test_unverified_user_cannot_create_organization(self):
        self.auth(self.make_user(verified=False))
        res = self.client.post('/api/v1/organizer/orgs/', {'name': 'Collectif', 'kind': 'collective'}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_superuser_is_verified(self):
        admin = User.objects.create_superuser('root@example.com', 'Str0ng-pass!42')
        self.assertTrue(admin.is_email_verified)
