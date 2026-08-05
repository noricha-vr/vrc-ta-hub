"""ログイン失敗レート制限の振る舞いテスト."""

from django.core.cache import cache
from django.test import Client, TestCase, override_settings, tag
from django.urls import reverse

from user_account.tests.utils import (
    TEST_SOCIALACCOUNT_PROVIDERS_WITH_APPS,
    create_discord_linked_user,
)


RATE_LIMIT_ERROR_CODE = 'too_many_login_attempts'
CLOUD_RUN_XFF = '203.0.113.10, 10.128.0.1'


def get_non_field_error_codes(response) -> set[str | None]:
    """レスポンスの認証フォームから非フィールドエラーcodeを返す。"""
    form = response.context['form']
    return {error.code for error in form.non_field_errors().as_data()}


@override_settings(SOCIALACCOUNT_PROVIDERS=TEST_SOCIALACCOUNT_PROVIDERS_WITH_APPS)
@tag('offline_external_api')
class LoginRateLimitTests(TestCase):
    """同一LoginView/Formを公開する2 URLで失敗制限を共有する."""

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
        forwarded_for: str | None = CLOUD_RUN_XFF,
    ):
        headers = {}
        if forwarded_for is not None:
            headers['HTTP_X_FORWARDED_FOR'] = forwarded_for
        return self.client.post(
            url,
            {'username': email, 'password': password},
            **headers,
        )

    def test_same_email_is_blocked_after_five_failures_across_login_paths(self) -> None:
        """同一View/Formの2 URLで同じemailバケットを数える."""
        responses = [self._post_login(self.login_url) for _ in range(3)]
        responses += [self._post_login(self.api_login_url) for _ in range(2)]

        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(
                RATE_LIMIT_ERROR_CODE,
                get_non_field_error_codes(response),
            )

        blocked = self._post_login(self.login_url)

        self.assertEqual(blocked.status_code, 200)
        self.assertIn(
            RATE_LIMIT_ERROR_CODE,
            get_non_field_error_codes(blocked),
        )

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
        self.assertNotIn(
            RATE_LIMIT_ERROR_CODE,
            get_non_field_error_codes(failed),
        )

    def test_missing_xff_does_not_raise_and_email_limit_still_blocks(self) -> None:
        """XFF欠落時はREMOTE_ADDRへ縮退し、500にせずemail制限を保つ."""
        responses = [
            self._post_login(self.login_url, forwarded_for=None)
            for _ in range(6)
        ]

        for response in responses[:5]:
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(
                RATE_LIMIT_ERROR_CODE,
                get_non_field_error_codes(response),
            )
        self.assertEqual(responses[-1].status_code, 200)
        self.assertIn(
            RATE_LIMIT_ERROR_CODE,
            get_non_field_error_codes(responses[-1]),
        )

    def test_spoofed_xff_prefix_does_not_bypass_ip_limit(self) -> None:
        """ユーザー供給prefixを変えてもCloud Run clientのIP制限を回避できない."""
        for attempt in range(10):
            response = self._post_login(
                self.login_url,
                email=f'absent-{attempt}@example.com',
                forwarded_for=f'198.51.100.{attempt}, 203.0.113.20, 10.128.0.1',
            )
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(
                RATE_LIMIT_ERROR_CODE,
                get_non_field_error_codes(response),
            )

        blocked = self._post_login(
            self.login_url,
            email='another-absent@example.com',
            forwarded_for='192.0.2.99, 203.0.113.20, 10.128.0.1',
        )

        self.assertEqual(blocked.status_code, 200)
        self.assertIn(
            RATE_LIMIT_ERROR_CODE,
            get_non_field_error_codes(blocked),
        )


@override_settings(SOCIALACCOUNT_PROVIDERS=TEST_SOCIALACCOUNT_PROVIDERS_WITH_APPS)
@tag('offline_external_api')
class AdminLoginRateLimitTests(TestCase):
    """最高権限のadminログインにもallauth失敗制限を適用する."""

    def setUp(self) -> None:
        cache.clear()
        self.client = Client()
        self.login_url = reverse('admin:login')
        self.user = create_discord_linked_user(
            user_name='rate_limit_admin',
            email='rate-limit-admin@example.com',
            password='testpass123',
        )
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=['is_staff', 'is_superuser'])

    def tearDown(self) -> None:
        cache.clear()

    def test_admin_login_is_blocked_after_five_failures(self) -> None:
        responses = [
            self.client.post(
                self.login_url,
                {
                    'username': self.user.email,
                    'password': 'wrong-password',
                },
                HTTP_X_FORWARDED_FOR=CLOUD_RUN_XFF,
            )
            for _ in range(6)
        ]

        for response in responses[:5]:
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(
                RATE_LIMIT_ERROR_CODE,
                get_non_field_error_codes(response),
            )
        self.assertEqual(responses[-1].status_code, 200)
        self.assertIn(
            RATE_LIMIT_ERROR_CODE,
            get_non_field_error_codes(responses[-1]),
        )
