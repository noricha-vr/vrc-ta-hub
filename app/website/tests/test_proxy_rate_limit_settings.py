"""Proxy経由レート制限のclient IP識別テスト。"""

from django.conf import settings
from django.test import RequestFactory, SimpleTestCase
from rest_framework.throttling import AnonRateThrottle


class DrfProxyIdentificationTests(SimpleTestCase):
    """DRF anon throttleをCloud Runの信頼済み2要素へ揃える。"""

    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.throttle = AnonRateThrottle()

    def _ident(self, forwarded_for: str | None) -> str:
        request = self.factory.get('/api/v1/events/')
        request.META['REMOTE_ADDR'] = '192.0.2.10'
        if forwarded_for is not None:
            request.META['HTTP_X_FORWARDED_FOR'] = forwarded_for
        return self.throttle.get_ident(request)

    def test_num_proxies_matches_allauth_contract(self) -> None:
        self.assertEqual(settings.REST_FRAMEWORK['NUM_PROXIES'], 2)
        self.assertEqual(settings.ALLAUTH_TRUSTED_PROXY_COUNT, 2)

    def test_missing_xff_uses_remote_addr(self) -> None:
        self.assertEqual(self._ident(None), '192.0.2.10')

    def test_single_xff_value_uses_that_value(self) -> None:
        self.assertEqual(self._ident('203.0.113.10'), '203.0.113.10')

    def test_two_xff_values_use_cloud_run_client(self) -> None:
        self.assertEqual(
            self._ident('203.0.113.10, 10.128.0.1'),
            '203.0.113.10',
        )

    def test_user_supplied_prefix_is_ignored(self) -> None:
        self.assertEqual(
            self._ident('198.51.100.99, 203.0.113.10, 10.128.0.1'),
            '203.0.113.10',
        )
