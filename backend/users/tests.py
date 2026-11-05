from config.testing import APITestCase


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
