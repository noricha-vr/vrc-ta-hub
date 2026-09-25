"""ローカル登録のメール列挙耐性・送信制限・確認待ち行の先取り対策を確認する（Issue #609）。"""

import re
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages import get_messages
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.core import mail
from django.core.cache import cache
from django.db import connection
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from allauth.account.models import EmailAddress, EmailConfirmationHMAC
from allauth.core.context import request_context
from allauth.socialaccount.internal.flows.signup import process_signup, signup_by_form
from allauth.socialaccount.models import SocialAccount, SocialLogin

from tests.factories import make_discord_linked_user, make_user, make_user_without_email_address
from user_account.adapters import CustomAccountAdapter, CustomSocialAccountAdapter
from user_account.forms import CustomSocialSignupForm, CustomUserChangeForm
from user_account.tests.utils import TEST_SOCIALACCOUNT_PROVIDERS, TEST_SOCIALACCOUNT_PROVIDERS_WITH_APPS

User = get_user_model()

SIGNUP_PASSWORD = 'Signup-Pass-2026!'
MISMATCHED_PASSWORD = 'Mismatch-Pass-2026!'
SQUATTER_PASSWORD = 'Squatter-Pass-2026!'
RECOVERED_PASSWORD = 'Recovered-Pass-2026!'
SIGNUP_IP_LIMIT = 20
CONFIRM_EMAIL_SENT_PATH = '/accounts/confirm-email/'
LOGIN_PATH = '/account/login/'
PASSWORD_RESET_PATH = '/accounts/password/reset/'
ACCOUNT_EXISTS_MAIL_PHRASE = '新しいアカウントは作成していません'
RESET_KEY_PATH_RE = re.compile(r'(/accounts/password/reset/key/[\w-]+/)')
# 本番の MySQL には作られない allauth の条件付き unique 制約（SQLite のテスト DB にだけ存在する）
PARTIAL_UNIQUE_INDEXES = ('unique_verified_email', 'unique_primary_email')
MAIL_AND_LOCAL_SIGNUP = {
    'EMAIL_BACKEND': 'django.core.mail.backends.locmem.EmailBackend',
    'SOCIALACCOUNT_PROVIDERS': TEST_SOCIALACCOUNT_PROVIDERS,
}


def signup_data(email, *, user_name='signup_user', password2=SIGNUP_PASSWORD, password=SIGNUP_PASSWORD):
    return {
        'user_name': user_name,
        'email': email,
        'password1': password,
        'password2': password2,
    }


def observed_response(response, email):
    """外から見える応答（ステータス・遷移先・Cookie 名・メッセージ）を email を伏せて返す。"""
    shown_messages = tuple(
        str(message).replace(email, '<email>')
        for message in get_messages(response.wsgi_request)
    )
    return (
        response.status_code,
        response.headers.get('Location'),
        tuple(sorted(response.cookies)),
        shown_messages,
    )


def make_unverified_user(user_name, email, password=SIGNUP_PASSWORD):
    user = make_user(user_name=user_name, email=email, password=password)
    EmailAddress.objects.filter(user=user).update(verified=False)
    return user


def add_pending_email_change(user, email):
    return EmailAddress.objects.create(user=user, email=email, verified=False, primary=False)


def confirm_email(client, address):
    url = reverse('account_confirm_email', args=[EmailConfirmationHMAC(address).key])
    client.get(url)
    return client.post(url)


def reset_key_path_from(body):
    """パスワード再設定メールの本文から、再設定リンクのパスを取り出す。"""
    match = RESET_KEY_PATH_RE.search(body)
    if match is None:
        raise AssertionError('password reset link was not found in the mail body')
    return match.group(1)


class CacheResetMixin:
    """allauth のレート制限はキャッシュに残るため、テストごとに消す。"""

    def setUp(self):
        super().setUp()
        cache.clear()

    def tearDown(self):
        cache.clear()
        super().tearDown()


@override_settings(**MAIL_AND_LOCAL_SIGNUP)
class SignupResponseUniformityTests(CacheResetMixin, TestCase):
    """登録済みかどうかで登録 POST の応答が変わらないことを確認する。"""

    def _register(self, email, **kwargs):
        return Client().post(reverse('account:register'), signup_data(email, **kwargs))

    def _new_signup_baseline(self):
        email = 'baseline-new@example.com'
        observed = observed_response(self._register(email, user_name='baseline_new'), email)
        self.assertEqual(observed[:2], (302, CONFIRM_EMAIL_SENT_PATH))
        self.assertEqual(len(observed[3]), 1)
        return observed

    def _assert_account_exists_mail(self, message, email):
        self.assertEqual(message.to, [email])
        self.assertIn(ACCOUNT_EXISTS_MAIL_PHRASE, message.body)
        self.assertIn(LOGIN_PATH, message.body)
        self.assertIn(PASSWORD_RESET_PATH, message.body)

    def test_registered_addresses_get_the_new_signup_response_and_a_guide_mail(self):
        """CustomUser.email のみ・確認済み・未確認のどれでも新規と同じ応答にし、案内メールを送る。"""
        baseline = self._new_signup_baseline()
        registered_states = (
            ('legacy-only@example.com', lambda email: make_user_without_email_address('legacy_only', email)),
            ('verified-owner@example.com', lambda email: make_user('verified_owner', email)),
            ('unverified-owner@example.com', lambda email: make_unverified_user('unverified_owner', email)),
        )
        for email, make_registered in registered_states:
            with self.subTest(email=email):
                make_registered(email)
                user_count = User.objects.count()
                sent_before = len(mail.outbox)

                response = self._register(email, user_name='second_signup')

                self.assertEqual(observed_response(response, email), baseline)
                self.assertEqual(User.objects.count(), user_count)
                self.assertEqual(len(mail.outbox), sent_before + 1)
                self._assert_account_exists_mail(mail.outbox[-1], email)

    def test_other_users_pending_change_does_not_block_signup(self):
        """他ユーザーの確認待ちの変更行があっても、新規と同じ応答で登録できる。"""
        baseline = self._new_signup_baseline()
        email = 'pending-elsewhere@example.com'
        add_pending_email_change(make_user('pending_owner', 'pending-owner@example.com'), email)

        response = self._register(email, user_name='pending_victim')

        self.assertEqual(observed_response(response, email), baseline)
        user = User.objects.get(email=email)
        self.assertTrue(EmailAddress.objects.filter(
            user=user, email=email, primary=True, verified=False,
        ).exists())
        self.assertEqual(mail.outbox[-1].to, [email])
        self.assertIn(CONFIRM_EMAIL_SENT_PATH, mail.outbox[-1].body)

    def test_registered_address_still_hashes_the_submitted_password(self):
        """新規登録との処理時間の差を縮めるため、登録済みでも入力パスワードをハッシュ化する。"""
        make_user('timing_owner', 'timing-owner@example.com')

        with patch('user_account.view_modules.session.make_password', wraps=make_password) as hashed:
            self._register('timing-owner@example.com')

        hashed.assert_called_once_with(SIGNUP_PASSWORD)

    def test_field_errors_do_not_depend_on_registration(self):
        """パスワード不一致などのエラー表示は、登録済みかどうかで変わらない。"""
        make_user('field_error_owner', 'field-error-owner@example.com')

        registered = self._register('field-error-owner@example.com', password2=MISMATCHED_PASSWORD)
        unregistered = self._register('field-error-new@example.com', password2=MISMATCHED_PASSWORD)

        self.assertEqual(registered.status_code, 200)
        self.assertEqual(unregistered.status_code, 200)
        self.assertEqual(
            registered.context['form'].errors.get_json_data(),
            unregistered.context['form'].errors.get_json_data(),
        )
        self.assertNotContains(registered, '既に登録')
        self.assertEqual(len(mail.outbox), 0)

    def test_mail_failure_response_does_not_depend_on_registration(self):
        """メール送信に失敗した時の応答も、登録済みかどうかで変わらない。"""
        make_user('mail_failure_owner', 'mail-failure-owner@example.com')
        with patch.object(CustomAccountAdapter, 'send_mail', side_effect=RuntimeError('simulated')):
            registered = self._register('mail-failure-owner@example.com')
            unregistered = self._register('mail-failure-new@example.com', user_name='mail_failure_new')

        self.assertEqual(
            observed_response(registered, 'mail-failure-owner@example.com'),
            observed_response(unregistered, 'mail-failure-new@example.com'),
        )
        self.assertEqual(registered.headers['Location'], reverse('account:login'))


@override_settings(**MAIL_AND_LOCAL_SIGNUP)
class SignupRateLimitTests(CacheResetMixin, TestCase):
    """登録 POST の IP 単位制限と、確認メールの宛先単位制限を確認する。"""

    def test_rate_limit_values_are_explicit(self):
        self.assertEqual(settings.ACCOUNT_RATE_LIMITS['signup'], f'{SIGNUP_IP_LIMIT}/m/ip')
        self.assertEqual(settings.ACCOUNT_RATE_LIMITS['confirm_email'], '1/180s/key')

    def test_signup_is_limited_per_ip_for_registered_and_new_addresses(self):
        """同一 IP の登録 POST は上限で 429 になり、登録済みかどうかで応答は変わらない。"""
        make_user('limited_owner', 'limited-owner@example.com')
        register_url = reverse('account:register')
        for _ in range(SIGNUP_IP_LIMIT):
            response = Client().post(register_url, signup_data('filler@example.com', password2=MISMATCHED_PASSWORD))
            self.assertEqual(response.status_code, 200)

        registered = Client().post(register_url, signup_data('limited-owner@example.com'))
        unregistered = Client().post(register_url, signup_data('limited-new@example.com'))

        self.assertEqual(registered.status_code, 429)
        self.assertEqual(unregistered.status_code, 429)
        self.assertEqual(registered.content, unregistered.content)
        self.assertFalse(User.objects.filter(email='limited-new@example.com').exists())
        self.assertEqual(len(mail.outbox), 0)
        other_ip = Client(REMOTE_ADDR='203.0.113.5').post(register_url, signup_data('limited-new@example.com'))
        self.assertEqual(other_ip.status_code, 302)

    def test_confirmation_mails_are_limited_per_address_without_revealing_registration(self):
        """確認メールの直後に同じ宛先で登録しても案内メールは送らず、応答は1回目と同じ。"""
        email = 'cooldown@example.com'
        register_url = reverse('account:register')
        first = observed_response(Client().post(register_url, signup_data(email)), email)

        throttled = Client().post(register_url, signup_data(email, user_name='cooldown_retry'))

        self.assertEqual(observed_response(throttled, email), first)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(User.objects.filter(email=email).count(), 1)

        cache.clear()  # 待ち時間が明けた状態に相当
        after_cooldown = Client().post(register_url, signup_data(email, user_name='cooldown_retry'))

        self.assertEqual(observed_response(after_cooldown, email), first)
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn(ACCOUNT_EXISTS_MAIL_PHRASE, mail.outbox[-1].body)

    def test_login_resend_shares_the_per_address_limit(self):
        """案内メール直後のログインでは確認メールを再送せず、待ち時間後は再送する。"""
        email = 'resend-owner@example.com'
        make_unverified_user('resend_owner', email)
        Client().post(reverse('account:register'), signup_data(email))
        credentials = {'username': email, 'password': SIGNUP_PASSWORD}

        response = Client().post(reverse('account:login'), credentials)

        self.assertRedirects(response, CONFIRM_EMAIL_SENT_PATH, fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)

        cache.clear()
        Client().post(reverse('account:login'), credentials)

        self.assertEqual(len(mail.outbox), 2)
        self.assertIn(CONFIRM_EMAIL_SENT_PATH, mail.outbox[-1].body)


@override_settings(**MAIL_AND_LOCAL_SIGNUP)
class PendingEmailChangeClaimTests(CacheResetMixin, TestCase):
    """他ユーザーの確認待ちの変更行だけは、各経路の重複判定に数えない。"""

    PENDING_EMAIL = 'claimed-by-pending@example.com'

    def setUp(self):
        super().setUp()
        add_pending_email_change(make_user('pending_owner', 'pending-owner@example.com'), self.PENDING_EMAIL)

    def _change_form(self, email):
        user = make_user('changing_user', 'changing-user@example.com')
        return CustomUserChangeForm(instance=user, data={
            'display_name': user.display_name,
            'user_name': user.user_name,
            'email': email,
            'x_account': '',
            'vrchat_user_id': '',
        })

    def _social_signup_form(self, email):
        return CustomSocialSignupForm(
            sociallogin=MagicMock(),
            data={'email': email, 'user_name': 'discord_new_user'},
        )

    def test_email_change_accepts_an_address_only_pending_elsewhere(self):
        form = self._change_form(self.PENDING_EMAIL)
        self.assertTrue(form.is_valid(), form.errors)

    def test_email_change_still_rejects_an_unverified_signup_address(self):
        make_unverified_user('signup_owner', 'signup-owner@example.com')
        form = self._change_form('signup-owner@example.com')
        self.assertFalse(form.is_valid())
        self.assertIn('このメールアドレスは既に登録されています。', form.errors['email'])

    def test_discord_signup_form_accepts_an_address_only_pending_elsewhere(self):
        form = self._social_signup_form(self.PENDING_EMAIL)
        self.assertTrue(form.is_valid(), form.errors)

    def test_discord_signup_form_rejects_a_verified_address_of_another_user(self):
        owner = make_user('verified_elsewhere', 'verified-elsewhere@example.com')
        EmailAddress.objects.create(user=owner, email='second-verified@example.com', verified=True, primary=False)
        form = self._social_signup_form('second-verified@example.com')
        self.assertFalse(form.is_valid())
        self.assertIn('このメールアドレスは既に登録されています', form.errors['email'][0])

    def test_discord_auto_signup_goes_to_the_form_when_only_pending_elsewhere(self):
        """allauth の自動登録判定は確認待ち行も衝突に数えるため、フォームへ回す。"""
        adapter = CustomSocialAccountAdapter()
        request = RequestFactory().get('/accounts/discord/login/callback/')
        pending_login = MagicMock()
        pending_login.account.extra_data = {'email': self.PENDING_EMAIL, 'verified': True}
        free_login = MagicMock()
        free_login.account.extra_data = {'email': 'free-discord@example.com', 'verified': True}

        self.assertFalse(adapter.is_auto_signup_allowed(request, pending_login))
        self.assertTrue(adapter.is_auto_signup_allowed(request, free_login))


@override_settings(
    EMAIL_BACKEND=MAIL_AND_LOCAL_SIGNUP['EMAIL_BACKEND'],
    SOCIALACCOUNT_PROVIDERS=TEST_SOCIALACCOUNT_PROVIDERS_WITH_APPS,
)
class DiscordSignupWithPendingChangeTests(CacheResetMixin, TestCase):
    """他ユーザーの確認待ちの変更行があっても、Discord 登録を最後まで完了できる。"""

    EMAIL = 'discord-owner@example.com'

    def setUp(self):
        super().setUp()
        self.pending = add_pending_email_change(make_user('pending_owner', 'pending-owner@example.com'), self.EMAIL)
        self.request = RequestFactory().get('/accounts/discord/login/callback/')
        self.request.user = AnonymousUser()
        SessionMiddleware(lambda request: None).process_request(self.request)
        self.request.session.save()
        MessageMiddleware(lambda request: None).process_request(self.request)
        self.sociallogin = SocialLogin(
            provider=CustomSocialAccountAdapter().get_provider(self.request, 'discord'),
            user=User(user_name='discord_owner', display_name='discord_owner', email=self.EMAIL),
            account=SocialAccount(
                provider='discord',
                uid='pending-conflict-uid',
                extra_data={'email': self.EMAIL, 'verified': True, 'username': 'discord_owner'},
            ),
            email_addresses=[EmailAddress(email=self.EMAIL, verified=True, primary=True)],
        )

    def _start_discord_signup(self):
        with request_context(self.request):
            self.sociallogin.state = SocialLogin.state_from_request(self.request)
            return process_signup(self.request, self.sociallogin)

    def _submit_signup_form(self):
        form = CustomSocialSignupForm(
            data={'email': self.EMAIL, 'user_name': 'discord_owner'},
            sociallogin=self.sociallogin,
        )
        self.assertTrue(form.is_valid(), form.errors)
        with request_context(self.request):
            return signup_by_form(self.request, self.sociallogin, form)

    def test_signup_goes_through_the_form_and_email_confirmation(self):
        response = self._start_discord_signup()

        self.assertEqual(response.headers['Location'], reverse('socialaccount_signup'))
        self.assertFalse(User.objects.filter(email=self.EMAIL).exists())
        self.assertEqual(len(mail.outbox), 0)

        self._submit_signup_form()

        user = User.objects.get(email=self.EMAIL)
        self.assertEqual(mail.outbox[-1].to, [self.EMAIL])
        address = EmailAddress.objects.get(user=user, email=self.EMAIL)
        self.assertFalse(address.verified)
        confirm_email(Client(), address)
        address.refresh_from_db()
        self.pending.refresh_from_db()
        self.assertTrue(address.verified)
        self.assertTrue(address.primary)
        self.assertFalse(self.pending.verified)


@override_settings(**MAIL_AND_LOCAL_SIGNUP)
class PendingEmailTakeoverTests(CacheResetMixin, TestCase):
    """確認待ちの変更行を持つ別アカウントが、登録者のアドレスを奪えないことを確認する。

    本番の MySQL には条件付き unique 制約が無いため、SQLite からも外してコード側の防御を確かめる。
    """

    VICTIM_EMAIL = 'mailbox-owner@example.com'
    ATTACKER_EMAIL = 'pending-attacker@example.com'

    def setUp(self):
        super().setUp()
        self._drop_partial_unique_indexes()
        self.attacker = make_discord_linked_user(user_name='pending_attacker', email=self.ATTACKER_EMAIL)
        self.attacker_client = Client()
        self.attacker_client.force_login(self.attacker)
        response = self.attacker_client.post(reverse('account:user_update'), {
            'display_name': self.attacker.display_name,
            'user_name': self.attacker.user_name,
            'email': self.VICTIM_EMAIL,
            'x_account': '',
            'vrchat_user_id': '',
        })
        self.assertEqual(response.status_code, 302)
        self.pending = EmailAddress.objects.get(user=self.attacker, email=self.VICTIM_EMAIL)
        cache.clear()  # 攻撃者の変更確認メールの送信制限が明けた後に登録する想定
        self.victim_client = Client()
        self.victim_client.post(reverse('account:register'), signup_data(self.VICTIM_EMAIL, user_name='mailbox_owner'))
        self.victim = User.objects.get(email=self.VICTIM_EMAIL)
        self.victim_address = EmailAddress.objects.get(user=self.victim, email=self.VICTIM_EMAIL)

    @staticmethod
    def _drop_partial_unique_indexes():
        """テストのトランザクションのロールバックで元に戻る。"""
        if connection.vendor != 'sqlite':
            return
        with connection.cursor() as cursor:
            for name in PARTIAL_UNIQUE_INDEXES:
                cursor.execute(f'DROP INDEX IF EXISTS "{name}"')

    def _assert_victim_keeps_address(self, *, verified):
        self.attacker.refresh_from_db()
        self.pending.refresh_from_db()
        self.victim.refresh_from_db()
        self.victim_address.refresh_from_db()
        self.assertEqual(self.attacker.email, self.ATTACKER_EMAIL)
        self.assertFalse(self.pending.verified)
        self.assertFalse(self.pending.primary)
        self.assertEqual(self.victim.email, self.VICTIM_EMAIL)
        self.assertTrue(self.victim_address.primary)
        self.assertEqual(self.victim_address.verified, verified)
        self.assertEqual(
            EmailAddress.objects.filter(email__iexact=self.VICTIM_EMAIL, verified=True).count(),
            int(verified),
        )

    def test_victim_signup_keeps_an_unverified_primary_address(self):
        self._assert_victim_keeps_address(verified=False)

    def test_pending_change_is_rejected_after_the_victim_verified(self):
        confirm_email(self.victim_client, self.victim_address)
        self._assert_victim_keeps_address(verified=True)

        with self.assertNoLogs('user_account.adapters', level='WARNING'):
            response = confirm_email(self.attacker_client, self.pending)

        self.assertEqual(response.status_code, 302)
        self._assert_victim_keeps_address(verified=True)

    def test_pending_change_is_rejected_while_the_victim_is_unverified(self):
        with self.assertNoLogs('user_account.adapters', level='WARNING'):
            response = confirm_email(self.attacker_client, self.pending)

        self.assertEqual(response.status_code, 302)
        self._assert_victim_keeps_address(verified=False)

        confirm_email(self.victim_client, self.victim_address)
        self._assert_victim_keeps_address(verified=True)

    def test_unique_violation_during_confirmation_fails_safely(self):
        """判定をすり抜ける競合でも 500 にせず、確認を取り消して失敗させる。"""
        with patch('user_account.adapters.is_email_in_use', return_value=False):
            with self.assertLogs('user_account.adapters', level='WARNING'):
                response = confirm_email(self.attacker_client, self.pending)

        self.assertEqual(response.status_code, 302)
        self._assert_victim_keeps_address(verified=False)


@override_settings(**MAIL_AND_LOCAL_SIGNUP)
class PreRegisteredAccountRecoveryTests(CacheResetMixin, TestCase):
    """他人が先に登録した未確認アカウントを、メールの持ち主がパスワード再設定で取り戻す。"""

    EMAIL = 'recovering-owner@example.com'

    def setUp(self):
        super().setUp()
        self.squatter_client = Client()
        self.squatter_client.post(reverse('account:register'), signup_data(
            self.EMAIL, user_name='squatter', password=SQUATTER_PASSWORD, password2=SQUATTER_PASSWORD,
        ))
        self.account = User.objects.get(email=self.EMAIL)
        self.owner_client = Client()
        cache.clear()  # 先取り登録の確認メールの送信制限が明けた後に持ち主が操作する想定

    def _reset_password_from_mail(self):
        self.assertEqual(self.owner_client.get(PASSWORD_RESET_PATH).status_code, 200)
        response = self.owner_client.post(PASSWORD_RESET_PATH, {'email': self.EMAIL})
        self.assertRedirects(response, reverse('account_reset_password_done'), fetch_redirect_response=False)
        reset_key_path = reset_key_path_from(mail.outbox[-1].body)
        set_password_path = self.owner_client.get(reset_key_path).headers['Location']
        self.assertEqual(self.owner_client.get(set_password_path).status_code, 200)
        response = self.owner_client.post(set_password_path, {
            'password1': RECOVERED_PASSWORD,
            'password2': RECOVERED_PASSWORD,
        })
        self.assertRedirects(
            response,
            reverse('account_reset_password_from_key_done'),
            fetch_redirect_response=False,
        )

    def test_owner_is_guided_to_password_reset_without_a_new_account(self):
        response = self.owner_client.post(reverse('account:register'), signup_data(self.EMAIL, user_name='owner'))

        self.assertRedirects(response, CONFIRM_EMAIL_SENT_PATH, fetch_redirect_response=False)
        self.assertEqual(User.objects.filter(email=self.EMAIL).count(), 1)
        self.assertIn(PASSWORD_RESET_PATH, mail.outbox[-1].body)

    def test_password_reset_takes_the_account_back_from_the_squatter(self):
        self._reset_password_from_mail()

        self.account.refresh_from_db()
        self.assertFalse(self.account.check_password(SQUATTER_PASSWORD))
        self.assertTrue(self.account.check_password(RECOVERED_PASSWORD))
        # allauth 65.18 のリンク式パスワード再設定はメールアドレスを確認済みにしない
        address = EmailAddress.objects.get(user=self.account, email=self.EMAIL)
        self.assertFalse(address.verified)
        response = self.squatter_client.post(reverse('account:login'), {
            'username': self.EMAIL,
            'password': SQUATTER_PASSWORD,
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.squatter_client.session)

    def test_owner_logs_in_after_reset_and_email_confirmation(self):
        self._reset_password_from_mail()

        response = self.owner_client.post(reverse('account:login'), {
            'username': self.EMAIL,
            'password': RECOVERED_PASSWORD,
        })

        self.assertRedirects(response, CONFIRM_EMAIL_SENT_PATH, fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.owner_client.session)
        self.assertIn(CONFIRM_EMAIL_SENT_PATH, mail.outbox[-1].body)
        address = EmailAddress.objects.get(user=self.account, email=self.EMAIL)
        confirm_email(self.owner_client, address)
        address.refresh_from_db()
        self.assertTrue(address.verified)
        self.assertEqual(self.owner_client.session['_auth_user_id'], str(self.account.pk))


@override_settings(**MAIL_AND_LOCAL_SIGNUP)
class SignupRaceTests(CacheResetMixin, TestCase):
    """重複判定と保存の間に同じアドレスが登録された競合を確認する。"""

    def test_concurrent_registration_of_the_same_email_gets_the_registered_response(self):
        """保存時の unique 違反は 500 にせず、登録済みと同じ応答と案内メールになる。"""
        email = 'race-owner@example.com'
        make_user('race_owner', email)
        user_count = User.objects.count()

        # 重複判定の時点では未登録だった状態を再現する
        with patch('user_account.forms.is_email_in_use', return_value=False):
            response = Client().post(reverse('account:register'), signup_data(email, user_name='race_new'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], CONFIRM_EMAIL_SENT_PATH)
        self.assertEqual(User.objects.count(), user_count)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(ACCOUNT_EXISTS_MAIL_PHRASE, mail.outbox[0].body)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class EmailChangeProbeRateLimitTests(CacheResetMixin, TestCase):
    """メール変更フォームで他人のアドレスを繰り返し照会できないことを確認する。"""

    def _post_email_change(self, user, email):
        return self.client.post(reverse('account:user_update'), {
            'display_name': user.display_name,
            'user_name': user.user_name,
            'email': email,
            'x_account': '',
            'vrchat_user_id': '',
        })

    @override_settings(ACCOUNT_RATE_LIMITS={**settings.ACCOUNT_RATE_LIMITS, 'manage_email': '2/m/user'})
    def test_duplicate_email_errors_consume_the_email_change_limit(self):
        make_user('probe_target', 'probe-target@example.com')
        prober = make_discord_linked_user(user_name='prober', email='prober@example.com')
        self.client.force_login(prober)

        for _ in range(2):
            response = self._post_email_change(prober, 'probe-target@example.com')
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'このメールアドレスは既に登録されています。')

        response = self._post_email_change(prober, 'prober-new@example.com')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'メールアドレスの変更回数が上限に達しました')
        self.assertFalse(EmailAddress.objects.filter(email='prober-new@example.com').exists())
