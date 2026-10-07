"""ログイン画面の Cloudflare Turnstile（ボット対策）の振る舞いテスト.

siteverify への通信は必ずモックする（外部に通信しない）。
fail-open のテストはモックが呼ばれた回数とログを確かめ、パッチ漏れで素通りしただけの緑を防ぐ。
Turnstile を通らずにパスワードを照合できる入口（allauth 標準のログイン・管理画面のログイン・
DRF の Basic 認証）が塞がっていることもここで確かめる。
"""

import base64
import uuid
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import requests
from allauth.account.auth_backends import AuthenticationBackend
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
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
from website.settings.authentication import (
    TURNSTILE_TEST_SECRET_KEY,
    TURNSTILE_TEST_SITE_KEY,
    resolve_turnstile_keys,
    validate_turnstile_keys,
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
# internal-error の時は 1 回だけ再試行する（初回 + 再試行 1 回）
INTERNAL_ERROR_ATTEMPTS = 2
ADMIN_INDEX_PATH = '/admin/'
PUBLIC_API_PATH = '/api/v1/community/'


def siteverify_response(body, status_code=200):
    """siteverify の応答を模したモックを返す。"""
    response = mock.Mock(status_code=status_code)
    response.json.return_value = body
    return response


def rejected_response(*error_codes):
    return siteverify_response({'success': False, 'error-codes': list(error_codes)})


PASSED_RESPONSE = siteverify_response({'success': True})
REJECTED_RESPONSE = rejected_response('invalid-input-response')
INTERNAL_ERROR_RESPONSE = rejected_response('internal-error')
SECRET_MISCONFIGURED_CODES = ('invalid-input-secret', 'missing-input-secret')


def get_non_field_error_codes(response) -> set[str | None]:
    """レスポンスの認証フォームから非フィールドエラーの code を返す。"""
    form = response.context['form']
    return {error.code for error in form.non_field_errors().as_data()}


def patch_password_backend():
    """パスワードを照合する認証バックエンドを、呼ばれたかどうかを記録するモックに差し替える。"""
    return mock.patch.object(AuthenticationBackend, 'authenticate', autospec=True, return_value=None)


def sent_idempotency_keys(siteverify) -> list[str]:
    return [call.kwargs['data']['idempotency_key'] for call in siteverify.call_args_list]


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

    def post_login(self, *, email=USER_EMAIL, password=USER_PASSWORD, token=None, url=None, extra=None):
        data = {'username': email, 'password': password, **(extra or {})}
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
        with mock.patch(SITEVERIFY_POST, return_value=REJECTED_RESPONSE) as siteverify, \
                patch_password_backend() as backend:
            response = self.post_login(token='forged-token')

        self.assert_rejected_by_turnstile(response)
        backend.assert_not_called()
        siteverify.assert_called_once()
        self.assertEqual(siteverify.call_args.args, (SITEVERIFY_URL,))
        self.assertEqual(siteverify.call_args.kwargs['timeout'], SITEVERIFY_TIMEOUT_SECONDS)
        self.assertIs(siteverify.call_args.kwargs['allow_redirects'], False)
        sent = siteverify.call_args.kwargs['data']
        self.assertEqual(
            {key: sent[key] for key in ('secret', 'response', 'remoteip')},
            {'secret': TEST_SECRET_KEY, 'response': 'forged-token', 'remoteip': TRUSTED_CLIENT_IP},
        )
        uuid.UUID(sent['idempotency_key'])

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

    def test_misconfigured_secret_rejects_login_with_the_ordinary_message(self) -> None:
        for error_code in SECRET_MISCONFIGURED_CODES:
            with self.subTest(error_code=error_code), \
                    mock.patch(SITEVERIFY_POST, return_value=rejected_response(error_code)), \
                    self.assertLogs(TURNSTILE_LOGGER, level='ERROR'):
                response = self.post_login(token=VALID_TOKEN)

                self.assert_rejected_by_turnstile(response)

    def test_misconfigured_secret_wins_over_internal_error(self) -> None:
        """internal-error と鍵エラーが併記されても、再試行→fail-open に流さず拒否する。"""
        for error_code in SECRET_MISCONFIGURED_CODES:
            mixed = rejected_response('internal-error', error_code)
            with self.subTest(error_code=error_code), \
                    mock.patch(SITEVERIFY_POST, return_value=mixed) as siteverify, \
                    self.assertLogs(TURNSTILE_LOGGER, level='ERROR'):
                response = self.post_login(token=VALID_TOKEN)

                self.assert_rejected_by_turnstile(response)
                self.assertEqual(siteverify.call_count, 1)

    def test_redirect_response_rejects_login(self) -> None:
        """3xx は障害ではないので fail-open にしない。"""
        redirect = siteverify_response(None, status_code=302)
        redirect.json.side_effect = ValueError
        with mock.patch(SITEVERIFY_POST, return_value=redirect), \
                self.assertLogs(TURNSTILE_LOGGER, level='WARNING'):
            response = self.post_login(token=VALID_TOKEN)

        self.assert_rejected_by_turnstile(response)

    def test_api_auth_login_url_also_requires_turnstile(self) -> None:
        with mock.patch(SITEVERIFY_POST) as siteverify:
            response = self.post_login(url=reverse('api-auth-login'))

        self.assert_rejected_by_turnstile(response)
        siteverify.assert_not_called()

    def test_internal_error_is_retried_once_with_the_same_idempotency_key(self) -> None:
        with mock.patch(SITEVERIFY_POST, side_effect=[INTERNAL_ERROR_RESPONSE, PASSED_RESPONSE]) as siteverify, \
                self.assertLogs(TURNSTILE_LOGGER, level='WARNING'):
            response = self.post_login(token=VALID_TOKEN)

        self.assert_logged_in(response)
        self.assertEqual(siteverify.call_count, INTERNAL_ERROR_ATTEMPTS)
        first_key, retry_key = sent_idempotency_keys(siteverify)
        self.assertEqual(first_key, retry_key)

    def test_cloudflare_outage_fails_open_with_warning(self) -> None:
        invalid_json_response = siteverify_response(None)
        invalid_json_response.json.side_effect = ValueError('not json')
        # (モックの設定, siteverify を呼ぶ回数)。internal-error だけは 1 回再試行してから通す
        outages = {
            'timeout': ({'side_effect': requests.Timeout()}, 1),
            'connection_error': ({'side_effect': requests.ConnectionError()}, 1),
            'server_error': ({'return_value': siteverify_response({}, status_code=503)}, 1),
            'invalid_json': ({'return_value': invalid_json_response}, 1),
            'internal_error_twice': ({'return_value': INTERNAL_ERROR_RESPONSE}, INTERNAL_ERROR_ATTEMPTS),
        }
        for name, (outage, expected_calls) in outages.items():
            with self.subTest(outage=name), mock.patch(SITEVERIFY_POST, **outage) as siteverify, \
                    self.assertLogs(TURNSTILE_LOGGER, level='WARNING'):
                response = self.post_login(token=VALID_TOKEN)

                self.assert_logged_in(response)
                self.assertEqual(siteverify.call_count, expected_calls)
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
class PasswordLoginEntryPointTests(TurnstileLoginTestCase):
    """Turnstile を通らずにパスワードを照合できる入口を塞いだ（公開ログイン画面へ一本化した）ことを確かめる."""

    def assert_redirected_to_public_login(self, response, next_path=None) -> None:
        expected = f'{self.login_url}?next={next_path}' if next_path else self.login_url
        self.assertRedirects(response, expected, fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_allauth_login_redirects_to_public_login_without_checking_password(self) -> None:
        allauth_login_url = f"{reverse('account_login')}?next=/account/settings/"

        with mock.patch(SITEVERIFY_POST) as siteverify, patch_password_backend() as backend:
            get_response = self.client.get(allauth_login_url)
            post_response = self.client.post(
                allauth_login_url,
                {'login': USER_EMAIL, 'password': USER_PASSWORD},
                HTTP_X_FORWARDED_FOR=CLOUD_RUN_XFF,
            )

        self.assert_redirected_to_public_login(get_response, next_path='/account/settings/')
        self.assert_redirected_to_public_login(post_response, next_path='/account/settings/')
        backend.assert_not_called()
        siteverify.assert_not_called()

    def test_admin_login_redirects_to_public_login_without_checking_password(self) -> None:
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        admin_login_url = f"{reverse('admin:login')}?next={ADMIN_INDEX_PATH}"

        with mock.patch(SITEVERIFY_POST) as siteverify, patch_password_backend() as backend:
            get_response = self.client.get(admin_login_url)
            post_response = self.client.post(
                admin_login_url,
                {'username': USER_EMAIL, 'password': USER_PASSWORD},
                HTTP_X_FORWARDED_FOR=CLOUD_RUN_XFF,
            )

        self.assert_redirected_to_public_login(get_response, next_path=ADMIN_INDEX_PATH)
        self.assert_redirected_to_public_login(post_response, next_path=ADMIN_INDEX_PATH)
        backend.assert_not_called()
        siteverify.assert_not_called()

    def test_anonymous_admin_access_ends_on_public_login(self) -> None:
        response = self.client.get(ADMIN_INDEX_PATH, follow=True)

        final_url = urlsplit(response.redirect_chain[-1][0])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(final_url.path, self.login_url)
        self.assertEqual(parse_qs(final_url.query), {'next': [ADMIN_INDEX_PATH]})
        self.assertTemplateUsed(response, 'account/login.html')

    def test_staff_logs_in_on_public_login_and_returns_to_admin(self) -> None:
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])

        with mock.patch(SITEVERIFY_POST, return_value=PASSED_RESPONSE):
            response = self.post_login(token=VALID_TOKEN, extra={'next': ADMIN_INDEX_PATH})

        self.assertRedirects(response, ADMIN_INDEX_PATH, fetch_redirect_response=False)
        self.assertEqual(self.client.session.get('_auth_user_id'), str(self.user.pk))

    def test_drf_basic_auth_does_not_check_passwords(self) -> None:
        def basic_auth(password):
            credentials = base64.b64encode(f'{USER_EMAIL}:{password}'.encode()).decode()
            return f'Basic {credentials}'

        with patch_password_backend() as backend:
            public_response = self.client.get(PUBLIC_API_PATH, HTTP_AUTHORIZATION=basic_auth('wrong-password'))
        protected_response = self.client.post(
            reverse('recurrence-preview'),
            {'base_date': '2026-01-01'},
            HTTP_AUTHORIZATION=basic_auth(USER_PASSWORD),
        )

        # 誤ったパスワードは照合されずに公開 API がそのまま返り、正しいパスワードでも認証済みにならない
        self.assertEqual(public_response.status_code, 200)
        backend.assert_not_called()
        self.assertEqual(protected_response.status_code, 403)


@override_settings(**TURNSTILE_KEYS)
class VerifyTurnstileTokenTests(SimpleTestCase):
    """siteverify の応答から判定結果への変換."""

    def verify(self, *responses, token=VALID_TOKEN):
        with mock.patch(SITEVERIFY_POST, side_effect=list(responses)) as siteverify:
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

    def test_only_boolean_true_success_on_http_200_passes(self) -> None:
        cases = {
            'success_true': ({'success': True}, 200, TurnstileResult.PASSED),
            'success_string': ({'success': 'true'}, 200, TurnstileResult.FAILED),
            'success_on_redirect': ({'success': True}, 302, TurnstileResult.FAILED),
            'duplicate_token': (
                {'success': False, 'error-codes': ['timeout-or-duplicate']}, 200, TurnstileResult.FAILED,
            ),
            'bad_request_4xx': ({'success': False, 'error-codes': ['bad-request']}, 400, TurnstileResult.FAILED),
        }
        for name, (body, status_code, expected) in cases.items():
            with self.subTest(case=name):
                result, siteverify = self.verify(siteverify_response(body, status_code=status_code))

                self.assertIs(result, expected)
                siteverify.assert_called_once()
                self.assertIs(siteverify.call_args.kwargs['allow_redirects'], False)

    def test_unexpected_body_is_unavailable_with_warning(self) -> None:
        with self.assertLogs(TURNSTILE_LOGGER, level='WARNING'):
            result, _ = self.verify(siteverify_response(['success']))

        self.assertIs(result, TurnstileResult.UNAVAILABLE)

    def test_non_json_4xx_is_rejected_without_fail_open(self) -> None:
        for status_code in (400, 401, 403):
            with self.subTest(status_code=status_code):
                response = siteverify_response(None, status_code=status_code)
                response.json.side_effect = ValueError('not json')

                result, siteverify = self.verify(response)

                self.assertIs(result, TurnstileResult.FAILED)
                siteverify.assert_called_once()

    def test_internal_error_with_another_error_code_is_rejected_without_retry(self) -> None:
        result, siteverify = self.verify(rejected_response('invalid-input-response', 'internal-error'))

        self.assertIs(result, TurnstileResult.FAILED)
        siteverify.assert_called_once()

    def test_each_verification_logs_result_and_error_codes_without_token(self) -> None:
        cases = {
            'passed': ((PASSED_RESPONSE,), VALID_TOKEN, 'passed', []),
            'rejected': ((REJECTED_RESPONSE,), VALID_TOKEN, 'failed', ['invalid-input-response']),
            'missing_token': ((), '', 'failed', ['missing-input-response']),
            'outage': ((requests.ConnectionError('down'),), VALID_TOKEN, 'unavailable', []),
        }
        for name, (responses, token, expected_result, expected_codes) in cases.items():
            with self.subTest(case=name), self.assertLogs(TURNSTILE_LOGGER, level='INFO') as logs:
                self.verify(*responses, token=token)

                records = [r for r in logs.records if hasattr(r, 'turnstile_result')]
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0].turnstile_result, expected_result)
                self.assertEqual(records[0].turnstile_error_codes, expected_codes)
                self.assertNotIn(VALID_TOKEN, '\n'.join(logs.output))
                self.assertNotIn(TEST_SECRET_KEY, '\n'.join(logs.output))

    def test_misconfigured_secret_fails_closed_with_error_log_without_secrets(self) -> None:
        for error_code in SECRET_MISCONFIGURED_CODES:
            with self.subTest(error_code=error_code), self.assertLogs(TURNSTILE_LOGGER, level='ERROR') as logs:
                result, _ = self.verify(rejected_response(error_code))

                self.assertIs(result, TurnstileResult.FAILED)
                # Sentry の送信前フィルタを通るよう is_silent を付ける
                self.assertIs(logs.records[0].is_silent, True)
                output = '\n'.join(logs.output)
                self.assertIn(error_code, output)
                self.assertNotIn(TEST_SECRET_KEY, output)
                self.assertNotIn(VALID_TOKEN, output)

    def test_internal_error_is_retried_once(self) -> None:
        cases = {
            'then_passed': ((INTERNAL_ERROR_RESPONSE, PASSED_RESPONSE), TurnstileResult.PASSED),
            'then_rejected': ((INTERNAL_ERROR_RESPONSE, REJECTED_RESPONSE), TurnstileResult.FAILED),
            'twice': ((INTERNAL_ERROR_RESPONSE, INTERNAL_ERROR_RESPONSE), TurnstileResult.UNAVAILABLE),
        }
        for name, (responses, expected) in cases.items():
            with self.subTest(case=name), self.assertLogs(TURNSTILE_LOGGER, level='WARNING'):
                result, siteverify = self.verify(*responses)

                self.assertIs(result, expected)
                self.assertEqual(siteverify.call_count, INTERNAL_ERROR_ATTEMPTS)
                self.assertEqual(len(set(sent_idempotency_keys(siteverify))), 1)


class ValidateTurnstileKeysTests(SimpleTestCase):
    """本番で鍵が片方だけの時に、ボット対策を黙って無効にせず起動を止める."""

    def test_only_one_key_in_production_raises(self) -> None:
        for site_key, secret_key in ((TEST_SITE_KEY, ''), ('', TEST_SECRET_KEY)):
            with self.subTest(site_key=site_key, secret_key=secret_key):
                with self.assertRaises(ImproperlyConfigured):
                    validate_turnstile_keys(site_key, secret_key, debug=False)

    def test_both_or_neither_key_or_debug_is_allowed(self) -> None:
        cases = (
            (TEST_SITE_KEY, TEST_SECRET_KEY, False),
            ('', '', False),
            (TEST_SITE_KEY, '', True),
            ('', TEST_SECRET_KEY, True),
        )
        for site_key, secret_key, debug in cases:
            with self.subTest(site_key=site_key, secret_key=secret_key, debug=debug):
                validate_turnstile_keys(site_key, secret_key, debug=debug)


class ResolveTurnstileKeysTests(SimpleTestCase):
    """開発（DEBUG）で鍵が無い時だけ Cloudflare のテスト用キー（必ず通る）に切り替える."""

    def test_debug_without_keys_uses_cloudflare_test_keys(self) -> None:
        self.assertEqual(
            resolve_turnstile_keys('', '', debug=True, use_test_keys=True),
            (TURNSTILE_TEST_SITE_KEY, TURNSTILE_TEST_SECRET_KEY),
        )

    def test_explicit_keys_production_or_opt_out_are_kept(self) -> None:
        cases = {
            # 本番で DEBUG を誤って有効にしても、明示した本物の鍵はテスト用キーに置き換えない
            'debug_with_keys': ((TEST_SITE_KEY, TEST_SECRET_KEY), True, True, (TEST_SITE_KEY, TEST_SECRET_KEY)),
            'production_without_keys': (('', ''), False, True, ('', '')),
            'debug_opt_out': (('', ''), True, False, ('', '')),
        }
        for name, ((site_key, secret_key), debug, use_test_keys, expected) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(
                    resolve_turnstile_keys(site_key, secret_key, debug=debug, use_test_keys=use_test_keys),
                    expected,
                )
