from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

User = get_user_model()

class UserAuthenticationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.staff_user = User.objects.create_user(
            email='staff@test.com',
            username='stafftest',
            password='Password123!',
            first_name='Staff',
            last_name='User',
            contact_number='1234567890',
            college='Tech University',
            gender='MALE',
            is_staff=True
        )
        self.regular_user = User.objects.create_user(
            email='regular@test.com',
            username='regulartest',
            password='Password123!',
            first_name='Regular',
            last_name='User',
            contact_number='1234567890',
            college='Tech University',
            gender='FEMALE',
            is_staff=False
        )

    def test_staff_login_successful(self):
        response = self.client.post(reverse('login'), {
            'email': 'staff@test.com',
            'password': 'Password123!'
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('problems:index'))

    def test_non_staff_login_denied(self):
        response = self.client.post(reverse('login'), {
            'email': 'regular@test.com',
            'password': 'Password123!'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'You do not have staff access.')

    def test_invalid_credentials(self):
        response = self.client.post(reverse('login'), {
            'email': 'staff@test.com',
            'password': 'WrongPassword'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Invalid email or password.')

    def test_logout(self):
        self.client.login(username='staff@test.com', password='Password123!')
        response = self.client.post(reverse('logout'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('login'))
