"""APIKey の有効期限・スコープ・IPホワイトリストに関するセキュリティテスト."""
from datetime import timedelta

from django.test import RequestFactory, TestCase
from django.utils import timezone

from user_account.models import APIKey, CustomUser


class APIKeyExpiryTests(TestCase):
    """expires_at による有効期限チェックのテスト."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            user_name='expiry_user',
            email='expiry@example.com',
            password='testpass123',
        )

    def test_no_expiry_is_treated_as_permanent(self):
        """expires_at=None のキーは無期限として扱われ is_valid()=True."""
        api_key, _raw = APIKey.create_with_raw_key(user=self.user, name='perm')
        self.assertIsNone(api_key.expires_at)
        self.assertFalse(api_key.is_expired())
        self.assertTrue(api_key.is_valid())

    def test_past_expires_at_invalidates_key(self):
        """expires_at が過去なら is_valid()=False."""
        api_key, _raw = APIKey.create_with_raw_key(user=self.user, name='past')
        api_key.expires_at = timezone.now() - timedelta(seconds=1)
        api_key.save(update_fields=['expires_at'])

        self.assertTrue(api_key.is_expired())
        self.assertFalse(api_key.is_valid())

    def test_future_expires_at_keeps_key_valid(self):
        """expires_at が未来なら is_valid()=True."""
        api_key, _raw = APIKey.create_with_raw_key(user=self.user, name='future')
        api_key.expires_at = timezone.now() + timedelta(days=30)
        api_key.save(update_fields=['expires_at'])

        self.assertFalse(api_key.is_expired())
        self.assertTrue(api_key.is_valid())


class APIKeyScopeTests(TestCase):
    """scope フィールドのデフォルト値とバリデーション."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            user_name='scope_user',
            email='scope@example.com',
            password='testpass123',
        )

    def test_default_scope_is_write_for_backward_compat(self):
        """既存キー互換性のためデフォルトは write."""
        api_key, _raw = APIKey.create_with_raw_key(user=self.user, name='default_scope')
        self.assertEqual(api_key.scope, APIKey.SCOPE_WRITE)


class APIKeyIPAllowlistTests(TestCase):
    """allowed_ips フィールドによるIPホワイトリストチェック."""

    def setUp(self):
        self.factory = RequestFactory()
        self.user = CustomUser.objects.create_user(
            user_name='ip_user',
            email='ip@example.com',
            password='testpass123',
        )
        self.api_key, _raw = APIKey.create_with_raw_key(user=self.user, name='ip_key')

    def _request_from(self, ip: str):
        request = self.factory.get('/api/')
        request.META['REMOTE_ADDR'] = ip
        return request

    def test_empty_allowlist_allows_all_ips(self):
        """allowed_ips が空ならどの IP からでも is_valid()=True."""
        self.assertEqual(self.api_key.allowed_ips, '')
        self.assertTrue(self.api_key.is_ip_allowed('203.0.113.5'))
        self.assertTrue(self.api_key.is_valid(self._request_from('203.0.113.5')))

    def test_allowlisted_ip_is_permitted(self):
        """allowed_ips に列挙された IP は許可。"""
        self.api_key.allowed_ips = '203.0.113.10, 198.51.100.1'
        self.api_key.save(update_fields=['allowed_ips'])

        self.assertTrue(self.api_key.is_ip_allowed('203.0.113.10'))
        self.assertTrue(self.api_key.is_valid(self._request_from('203.0.113.10')))

    def test_non_allowlisted_ip_is_rejected(self):
        """allowed_ips に含まれない IP は拒否。"""
        self.api_key.allowed_ips = '203.0.113.10'
        self.api_key.save(update_fields=['allowed_ips'])

        self.assertFalse(self.api_key.is_ip_allowed('198.51.100.99'))
        self.assertFalse(self.api_key.is_valid(self._request_from('198.51.100.99')))

    def test_cidr_range_is_supported(self):
        """CIDR 表記の範囲指定が機能する."""
        self.api_key.allowed_ips = '192.168.1.0/24'
        self.api_key.save(update_fields=['allowed_ips'])

        # 範囲内
        self.assertTrue(self.api_key.is_ip_allowed('192.168.1.1'))
        self.assertTrue(self.api_key.is_ip_allowed('192.168.1.254'))
        # 範囲外
        self.assertFalse(self.api_key.is_ip_allowed('192.168.2.1'))
        self.assertFalse(self.api_key.is_ip_allowed('10.0.0.1'))

    def test_invalid_client_ip_is_rejected_when_allowlist_set(self):
        """allowlist 設定済みで不正な client IP の場合は fail-closed."""
        self.api_key.allowed_ips = '203.0.113.0/24'
        self.api_key.save(update_fields=['allowed_ips'])

        self.assertFalse(self.api_key.is_ip_allowed('not-an-ip'))
        self.assertFalse(self.api_key.is_ip_allowed(''))
