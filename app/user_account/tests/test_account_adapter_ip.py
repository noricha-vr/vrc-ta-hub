"""allauth account adapterのclient IP識別テスト。"""

from django.http import HttpRequest
from django.test import RequestFactory, SimpleTestCase

from user_account.adapters import CustomAccountAdapter


class CustomAccountAdapterClientIpTests(SimpleTestCase):
    """allauthのIPキーをCloud RunのXFF契約から安全に取得する。"""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.adapter = CustomAccountAdapter()

    def _request(self, forwarded_for: str | None) -> HttpRequest:
        request = self.factory.post('/account/login/')
        request.META['REMOTE_ADDR'] = '192.0.2.10'
        if forwarded_for is not None:
            request.META['HTTP_X_FORWARDED_FOR'] = forwarded_for
        return request

    def test_missing_xff_uses_remote_addr(self) -> None:
        request = self._request(None)

        self.assertEqual(self.adapter.get_client_ip(request), '192.0.2.10')

    def test_single_xff_value_uses_that_value(self) -> None:
        request = self._request('203.0.113.10')

        self.assertEqual(self.adapter.get_client_ip(request), '203.0.113.10')

    def test_two_xff_values_use_cloud_run_client(self) -> None:
        request = self._request('203.0.113.10, 10.128.0.1')

        self.assertEqual(self.adapter.get_client_ip(request), '203.0.113.10')

    def test_spoofed_xff_prefix_is_ignored(self) -> None:
        request = self._request('198.51.100.99, 203.0.113.10, 10.128.0.1')

        self.assertEqual(self.adapter.get_client_ip(request), '203.0.113.10')
