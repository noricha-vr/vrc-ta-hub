"""ログイン失敗レート制限の振る舞いテスト."""

from django.core.cache import cache
from django.test import Client, TestCase, override_settings, tag
from django.urls import reverse

from user_account.tests.utils import (
    TEST_SOCIALACCOUNT_PROVIDERS_WITH_APPS,
    create_discord_linked_user,
)


RATE_LIMIT_MESSAGE = 'ログイン失敗が連続しています。時間が経ってからやり直してください。'
CLOUD_RUN_XFF = '203.0.113.10, 10.128.0.1'


@override_settings(SOCIALACCOUNT_PROVIDERS=TEST_SOCIALACCOUNT_PROVIDERS_WITH_APPS)
@tag('offline_external_api')
class LoginRateLimitTests(TestCase):
    """通常画面とDRF画面に同じログイン失敗制限を適用する."""

    def setUp(self) -> None:
        cache.clear()
        self.client = Client()
        self.login_url = reverse('account:login')
        self.api_login_url = reverse('api-auth-login')
        self.user = create_discord_linked_user(
            user_name='rate_limit_user',
            email='rate-limit@example.com',
            password='testpass123',
        )

    def tearDown(self) -> None:
        cache.clear()

    def _post_login(
        self,
        url: str,
        *,
        email: str = 'rate-limit@example.com',
        password: str = 'wrong-password',
        forwarded_for: str = CLOUD_RUN_XFF,
    ):
        return self.client.post(
            url,
            {'username': email, 'password': password},
            HTTP_X_FORWARDED_FOR=forwarded_for,
        )

    def test_same_email_is_blocked_after_five_failures_across_login_paths(self) -> None:
        """通常/API経路の失敗を同じemailバケットで数える."""
        responses = [self._post_login(self.login_url) for _ in range(3)]
        responses += [self._post_login(self.api_login_url) for _ in range(2)]

        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, RATE_LIMIT_MESSAGE)

        blocked = self._post_login(self.login_url)

        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, RATE_LIMIT_MESSAGE)

    def test_cache_reset_recovers_a_locked_out_user(self) -> None:
        """期限切れ相当のカウンタ消去後は正しい認証を再開できる."""
        for _ in range(6):
            self._post_login(self.login_url)
        cache.clear()

        response = self._post_login(
            self.login_url,
            password='testpass123',
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session['_auth_user_id'], str(self.user.pk))

    def test_successful_logins_do_not_increase_failure_counter(self) -> None:
        """成功時rollbackにより後続の初回失敗をブロックしない."""
        for _ in range(5):
            response = self._post_login(
                self.login_url,
                password='testpass123',
            )
            self.assertEqual(response.status_code, 302)
            self.client.logout()

        failed = self._post_login(self.login_url)

        self.assertEqual(failed.status_code, 200)
        self.assertNotContains(failed, RATE_LIMIT_MESSAGE)

    def test_spoofed_xff_prefix_does_not_bypass_ip_limit(self) -> None:
        """Cloud Run XFFの左側を変えてもclient IPを共有する."""
        for attempt in range(10):
            response = self._post_login(
                self.login_url,
                email=f'absent-{attempt}@example.com',
                forwarded_for=f'198.51.100.{attempt}, 203.0.113.20, 10.128.0.1',
            )
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, RATE_LIMIT_MESSAGE)

        blocked = self._post_login(
            self.login_url,
            email='another-absent@example.com',
            forwarded_for='192.0.2.99, 203.0.113.20, 10.128.0.1',
        )

        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, RATE_LIMIT_MESSAGE)
