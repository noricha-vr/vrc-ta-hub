from django.test import TestCase
from rest_framework.test import APIClient

from api_v1.models import APIRequestLog
from user_account.models import APIKey, CustomUser


class APIRequestLogMiddlewareTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = CustomUser.objects.create_user(
            user_name='log_test_user',
            email='log_test@example.com',
            password='pass12345',
        )

    def test_anonymous_request_is_logged(self):
        response = self.client.get('/api/v1/community/')
        self.assertEqual(response.status_code, 200)

        log = APIRequestLog.objects.get()
        self.assertEqual(log.auth_method, APIRequestLog.AUTH_ANONYMOUS)
        self.assertIsNone(log.user)
        self.assertIsNone(log.api_key)
        self.assertEqual(log.path, '/api/v1/community/')
        self.assertEqual(log.method, 'GET')
        self.assertEqual(log.status_code, 200)

    def test_session_authenticated_request_is_logged(self):
        self.client.force_login(self.user)
        response = self.client.get('/api/v1/community/')
        self.assertEqual(response.status_code, 200)

        log = APIRequestLog.objects.get()
        self.assertEqual(log.auth_method, APIRequestLog.AUTH_SESSION)
        self.assertEqual(log.user, self.user)
        self.assertIsNone(log.api_key)

    def test_api_key_authenticated_request_is_logged(self):
        api_key_obj, raw_key = APIKey.create_with_raw_key(
            user=self.user, name='log-test'
        )
        response = self.client.get(
            '/api/v1/event-details/',
            HTTP_AUTHORIZATION=f'Bearer {raw_key}',
        )
        # 認証は通るがオーナー権限で結果は絞られる。ステータスは 200 想定
        self.assertIn(response.status_code, (200, 403))

        log = APIRequestLog.objects.get()
        self.assertEqual(log.auth_method, APIRequestLog.AUTH_API_KEY)
        self.assertEqual(log.user, self.user)
        self.assertEqual(log.api_key, api_key_obj)

    def test_non_api_path_is_not_logged(self):
        self.client.get('/')
        self.assertEqual(APIRequestLog.objects.count(), 0)

    def test_ip_and_user_agent_are_captured(self):
        response = self.client.get(
            '/api/v1/community/',
            HTTP_USER_AGENT='TestAgent/1.0',
            REMOTE_ADDR='198.51.100.10',
        )
        self.assertEqual(response.status_code, 200)

        log = APIRequestLog.objects.get()
        self.assertEqual(log.user_agent, 'TestAgent/1.0')
        self.assertEqual(log.remote_ip, '198.51.100.10')
