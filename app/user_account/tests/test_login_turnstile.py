"""ログイン画面の Cloudflare Turnstile（ボット対策）の振る舞いテスト.

siteverify への通信は必ずモックする（外部に通信しない）。
fail-open のテストはモックが呼ばれたこととログを確かめ、パッチ漏れで素通りしただけの緑を防ぐ。
"""

from unittest import mock

import requests
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings, tag
from django.urls import reverse

from user_account.forms import TURNSTILE_FAILED_ERROR_CODE, TURNSTILE_FAILED_MESSAGE
from user_account.tests.utils import (
    TEST_SOCIALACCOUNT_PROVIDERS_WITH_APPS,
    create_discord_linked_user,
)
from user_account.turnstile import (
    MAX_TOKEN_LENGTH,
    SITEVERIFY_TIMEOUT_SECONDS,
    SITEVERIFY_URL,
    TurnstileResult,
    verify_turnstile_token,
)

TEST_SITE_KEY = 'test-turnstile-site-key'
TEST_SECRET_KEY = 'test-turnstile-secret-key'
TURNSTILE_KEYS = {
    'TURNSTILE_SITE_KEY': TEST_SITE_KEY,
    'TURNSTILE_SECRET_KEY': TEST_SECRET_KEY,
}
SITEVERIFY_POST = 'user_account.turnstile.requests.post'
TURNSTILE_LOGGER = 'user_account.turnstile'
WIDGET_SCRIPT_URL = 'https://challenges.cloudflare.com/turnstile/v0/api.js'
RATE_LIMIT_ERROR_CODE = 'too_many_login_attempts'
INVALID_LOGIN_ERROR_CODE = 'invalid_login'
# ログイン失敗の回数制限（settings の login_failed: 同一 email は 5 回、同一 IP は 10 回）
EMAIL_FAILURE_LIMIT = 5
IP_FAILURE_LIMIT = 10
CLOUD_RUN_XFF = '203.0.113.10, 10.128.0.1'
TRUSTED_CLIENT_IP = '203.0.113.10'
USER_EMAIL = 'turnstile@example.com'
USER_PASSWORD = 'testpass123'
VALID_TOKEN = 'valid-token'


def siteverify_response(body, status_code=200):
    """siteverify の応答を模したモックを返す。"""
    response = mock.Mock(status_code=status_code)
    response.json.return_value = body
    return response


PASSED_RESPONSE = siteverify_response({'success': True})
REJECTED_RESPONSE = siteverify_response(
    {'success': False, 'error-codes': ['invalid-input-response']},
)


def get_non_field_error_codes(response) -> set[str | None]:
    """レスポンスの認証フォームから非フィールドエラーの code を返す。"""
    form = response.context['form']
    return {error.code for error in form.non_field_errors().as_data()}


@override_settings(SOCIALACCOUNT_PROVIDERS=TEST_SOCIALACCOUNT_PROVIDERS_WITH_APPS)
@tag('offline_external_api')
class TurnstileLoginTestCase(TestCase):
    """ログイン POST の共通手順."""

    def setUp(self) -> None:
        cache.clear()
        self.client = Client()
        self.login_url = reverse('account:login')
        self.user = create_discord_linked_user(
            user_name='turnstile_user',
            email=USER_EMAIL,
            password=USER_PASSWORD,
        )

    def tearDown(self) -> None:
        cache.clear()

    def post_login(self, *, email=USER_EMAIL, password=USER_PASSWORD, token=None, url=None):
        data = {'username': email, 'password': password}
        if token is not None:
            data['cf-turnstile-response'] = token
        return self.client.post(url or self.login_url, data, HTTP_X_FORWARDED_FOR=CLOUD_RUN_XFF)

    def assert_logged_in(self, response) -> None:
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get('_auth_user_id'), str(self.user.pk))

    def assert_rejected_by_turnstile(self, response) -> None:
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(get_non_field_error_codes(response), {TURNSTILE_FAILED_ERROR_CODE})
        self.assertContains(response, TURNSTILE_FAILED_MESSAGE)

    def assert_rate_limited(self, response) -> None:
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertIn(RATE_LIMIT_ERROR_CODE, get_non_field_error_codes(response))


class TurnstileDisabledLoginTests(TurnstileLoginTestCase):
    """鍵が片方でも未設定なら、ウィジェットを出さず今までどおりログインできる."""

    KEY_PATTERNS = (
        {'TURNSTILE_SITE_KEY': '', 'TURNSTILE_SECRET_KEY': ''},
        {'TURNSTILE_SITE_KEY': TEST_SITE_KEY, 'TURNSTILE_SECRET_KEY': ''},
        {'TURNSTILE_SITE_KEY': '', 'TURNSTILE_SECRET_KEY': TEST_SECRET_KEY},
    )

    def test_login_page_has_no_widget(self) -> None:
        for keys in self.KEY_PATTERNS:
            with self.subTest(keys=keys), override_settings(**keys):
                response = self.client.get(self.login_url)

                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'cf-turnstile')
                self.assertNotContains(response, 'challenges.cloudflare.com')

    def test_login_without_token_succeeds_without_siteverify(self) -> None:
        for keys in self.KEY_PATTERNS:
            with self.subTest(keys=keys), override_settings(**keys), \
                    mock.patch(SITEVERIFY_POST) as siteverify:
                response = self.post_login()

                self.assert_logged_in(response)
                siteverify.assert_not_called()
                self.client.logout()


@override_settings(**TURNSTILE_KEYS)
class TurnstileEnabledLoginTests(TurnstileLoginTestCase):
    """鍵が 2 つとも設定済みなら、siteverify に通ったトークンでだけログインできる."""

    def test_login_page_renders_widget_script_and_noscript_notice(self) -> None:
        response = self.client.get(self.login_url)

        self.assertContains(response, f'data-sitekey="{TEST_SITE_KEY}"')
        self.assertContains(response, f'<script src="{WIDGET_SCRIPT_URL}" async defer></script>', html=True)
        self.assertContains(response, 'aria-describedby="turnstile-help"')
        self.assertContains(response, '<noscript>')
        self.assertNotContains(response, TEST_SECRET_KEY)

    def test_missing_or_empty_token_is_rejected_without_siteverify(self) -> None:
        for token in (None, ''):
            with self.subTest(token=token), mock.patch(SITEVERIFY_POST) as siteverify:
                response = self.post_login(token=token)

                self.assert_rejected_by_turnstile(response)
                siteverify.assert_not_called()

    def test_rejected_token_blocks_login_even_with_correct_password(self) -> None:
        with mock.patch(SITEVERIFY_POST, return_value=REJECTED_RESPONSE) as siteverify:
            response = self.post_login(token='forged-token')

        self.assert_rejected_by_turnstile(response)
        siteverify.assert_called_once_with(
            SITEVERIFY_URL,
            data={'secret': TEST_SECRET_KEY, 'response': 'forged-token', 'remoteip': TRUSTED_CLIENT_IP},
            timeout=SITEVERIFY_TIMEOUT_SECONDS,
        )

    def test_passed_token_logs_in(self) -> None:
        with mock.patch(SITEVERIFY_POST, return_value=PASSED_RESPONSE) as siteverify:
            response = self.post_login(token=VALID_TOKEN)

        self.assert_logged_in(response)
        siteverify.assert_called_once()

    def test_passed_token_with_wrong_password_is_an_ordinary_login_failure(self) -> None:
        with mock.patch(SITEVERIFY_POST, return_value=PASSED_RESPONSE):
            response = self.post_login(password='wrong-password', token=VALID_TOKEN)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(get_non_field_error_codes(response), {INVALID_LOGIN_ERROR_CODE})
        self.assertNotContains(response, TURNSTILE_FAILED_MESSAGE)

    def test_api_auth_login_url_also_requires_turnstile(self) -> None:
        with mock.patch(SITEVERIFY_POST) as siteverify:
            response = self.post_login(url=reverse('api-auth-login'))

        self.assert_rejected_by_turnstile(response)
        siteverify.assert_not_called()

    def test_admin_login_does_not_require_turnstile(self) -> None:
        """管理画面のログインにはウィジェットが無いので、Turnstile の検証をしない."""
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])

        with mock.patch(SITEVERIFY_POST) as siteverify:
            response = self.client.post(
                reverse('admin:login'),
                {'username': USER_EMAIL, 'password': USER_PASSWORD},
                HTTP_X_FORWARDED_FOR=CLOUD_RUN_XFF,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get('_auth_user_id'), str(self.user.pk))
        siteverify.assert_not_called()

    def test_cloudflare_outage_fails_open_with_warning(self) -> None:
        invalid_json_response = siteverify_response(None)
        invalid_json_response.json.side_effect = ValueError('not json')
        outages = {
            'timeout': {'side_effect': requests.Timeout()},
            'connection_error': {'side_effect': requests.ConnectionError()},
            'server_error': {'return_value': siteverify_response({}, status_code=503)},
            'invalid_json': {'return_value': invalid_json_response},
            'internal_error': {
                'return_value': siteverify_response({'success': False, 'error-codes': ['internal-error']}),
            },
        }
        for name, outage in outages.items():
            with self.subTest(outage=name), mock.patch(SITEVERIFY_POST, **outage) as siteverify, \
                    self.assertLogs(TURNSTILE_LOGGER, level='WARNING'):
                response = self.post_login(token=VALID_TOKEN)

                self.assert_logged_in(response)
                siteverify.assert_called_once()
                self.client.logout()


@override_settings(**TURNSTILE_KEYS)
class TurnstileLoginRateLimitTests(TurnstileLoginTestCase):
    """Turnstile の失敗もログイン失敗の回数制限（#594）に数える."""

    def test_turnstile_failures_count_toward_email_limit(self) -> None:
        for _ in range(EMAIL_FAILURE_LIMIT):
            self.assert_rejected_by_turnstile(self.post_login())

        with mock.patch(SITEVERIFY_POST, return_value=PASSED_RESPONSE) as siteverify:
            blocked = self.post_login(token=VALID_TOKEN)

        self.assert_rate_limited(blocked)
        # 回数制限を先に判定するので、制限中は Cloudflare に問い合わせない
        siteverify.assert_not_called()

    def test_turnstile_failures_count_toward_ip_limit(self) -> None:
        for attempt in range(IP_FAILURE_LIMIT):
            self.assert_rejected_by_turnstile(self.post_login(email=f'absent-{attempt}@example.com'))

        with mock.patch(SITEVERIFY_POST, return_value=PASSED_RESPONSE) as siteverify:
            blocked = self.post_login(token=VALID_TOKEN)

        self.assert_rate_limited(blocked)
        siteverify.assert_not_called()

    def test_turnstile_and_password_failures_share_one_counter(self) -> None:
        with mock.patch(SITEVERIFY_POST, return_value=PASSED_RESPONSE):
            for _ in range(3):
                response = self.post_login(password='wrong-password', token=VALID_TOKEN)
                self.assertEqual(get_non_field_error_codes(response), {INVALID_LOGIN_ERROR_CODE})
        with mock.patch(SITEVERIFY_POST, return_value=REJECTED_RESPONSE):
            for _ in range(2):
                self.assert_rejected_by_turnstile(self.post_login(token='forged-token'))

        with mock.patch(SITEVERIFY_POST, return_value=PASSED_RESPONSE):
            blocked = self.post_login(token=VALID_TOKEN)

        self.assert_rate_limited(blocked)

    def test_successful_logins_do_not_consume_the_counter(self) -> None:
        with mock.patch(SITEVERIFY_POST, return_value=PASSED_RESPONSE):
            for _ in range(EMAIL_FAILURE_LIMIT + 1):
                self.assert_logged_in(self.post_login(token=VALID_TOKEN))
                self.client.logout()

        response = self.post_login()

        self.assert_rejected_by_turnstile(response)


@override_settings(**TURNSTILE_KEYS)
class VerifyTurnstileTokenTests(SimpleTestCase):
    """siteverify の応答から判定結果への変換."""

    def verify(self, response, token=VALID_TOKEN):
        with mock.patch(SITEVERIFY_POST, return_value=response) as siteverify:
            result = verify_turnstile_token(token, TRUSTED_CLIENT_IP)
        return result, siteverify

    def test_token_length_is_checked_before_siteverify(self) -> None:
        cases = {
            '': TurnstileResult.FAILED,
            'x' * (MAX_TOKEN_LENGTH + 1): TurnstileResult.FAILED,
            'x' * MAX_TOKEN_LENGTH: TurnstileResult.PASSED,
        }
        for token, expected in cases.items():
            with self.subTest(token_length=len(token)):
                result, siteverify = self.verify(PASSED_RESPONSE, token=token)

                self.assertIs(result, expected)
                self.assertEqual(siteverify.called, expected is TurnstileResult.PASSED)

    def test_only_boolean_true_success_passes(self) -> None:
        cases = {
            'success_true': ({'success': True}, TurnstileResult.PASSED),
            'success_string': ({'success': 'true'}, TurnstileResult.FAILED),
            'duplicate_token': (
                {'success': False, 'error-codes': ['timeout-or-duplicate']},
                TurnstileResult.FAILED,
            ),
            'bad_request_4xx': (
                {'success': False, 'error-codes': ['bad-request']},
                TurnstileResult.FAILED,
            ),
        }
        for name, (body, expected) in cases.items():
            with self.subTest(case=name):
                status_code = 400 if name == 'bad_request_4xx' else 200
                result, _ = self.verify(siteverify_response(body, status_code=status_code))

                self.assertIs(result, expected)

    def test_unexpected_body_is_unavailable_with_warning(self) -> None:
        with self.assertLogs(TURNSTILE_LOGGER, level='WARNING'):
            result, _ = self.verify(siteverify_response(['success']))

        self.assertIs(result, TurnstileResult.UNAVAILABLE)

    def test_misconfigured_secret_is_logged_as_error_without_secrets(self) -> None:
        response = siteverify_response({'success': False, 'error-codes': ['invalid-input-secret']})

        with self.assertLogs(TURNSTILE_LOGGER, level='ERROR') as logs:
            result, _ = self.verify(response)

        self.assertIs(result, TurnstileResult.UNAVAILABLE)
        output = '\n'.join(logs.output)
        self.assertIn('invalid-input-secret', output)
        self.assertNotIn(TEST_SECRET_KEY, output)
        self.assertNotIn(VALID_TOKEN, output)
