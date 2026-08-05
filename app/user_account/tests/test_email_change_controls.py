"""メールアドレス変更の公開経路を回帰テストする。"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from allauth.account.models import EmailAddress

from tests.factories import make_user, make_user_without_email_address
from user_account.adapters import CustomAccountAdapter
from user_account.tests.utils import (
    TEST_SOCIALACCOUNT_PROVIDERS,
    create_discord_linked_user,
)

User = get_user_model()
EMAIL_CHANGE_LIMIT = 10


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class LoginMessageTests(TestCase):
    """ログイン成功メッセージの表示回数を確認する。"""

    def test_custom_login_does_not_add_a_duplicate_success_message(self):
        """カスタムビューがログイン成功メッセージを重複して追加しない。"""
        user = create_discord_linked_user(
            user_name='login_message_user',
            email='login-message@example.com',
            password='testpass123',
        )

        response = self.client.post(reverse('account:login'), {
            'username': user.email,
            'password': 'testpass123',
        })

        login_messages = [
            str(message)
            for message in get_messages(response.wsgi_request)
            if 'ログインしました' in str(message)
        ]
        self.assertEqual(len(login_messages), 1)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class UserNameChangeViewTests(TestCase):
    """ユーザー名変更画面がメールアドレスを受け付けないことを確認する。"""

    def setUp(self):
        self.client = Client()
        self.user = create_discord_linked_user(
            user_name='name_change_user',
            email='name-change@example.com',
            password='testpass123',
        )
        self.url = reverse('account:user_name_change')
        self.client.force_login(self.user)

    def test_user_name_change_form_does_not_render_email_field(self):
        """ユーザー名変更画面にはメールアドレス入力を表示しない。"""
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="email"')

    def test_user_name_change_ignores_tampered_email_post_value(self):
        """改ざんされたメールアドレスをPOSTしても保存しない。"""
        response = self.client.post(self.url, {
            'user_name': 'updated_name',
            'email': 'tampered@example.com',
        })

        self.assertRedirects(response, reverse('account:settings'), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertEqual(self.user.user_name, 'updated_name')
        self.assertEqual(self.user.email, 'name-change@example.com')
        self.assertFalse(EmailAddress.objects.filter(
            user=self.user,
            email='tampered@example.com',
        ).exists())


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class EmailChangeFlowTests(TestCase):
    """プロフィールと管理画面のメールアドレス変更を確認する。"""

    def setUp(self):
        cache.clear()
        self.client = Client()

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def test_user_update_creates_a_pending_email_address(self):
        """プロフィール更新は確認前のアドレスを作り現在のログインを維持する。"""
        user = create_discord_linked_user(
            user_name='profile_change_user',
            email='profile-change@example.com',
            password='testpass123',
        )
        self.client.force_login(user)

        response = self.client.post(reverse('account:user_update'), {
            'display_name': user.display_name,
            'user_name': user.user_name,
            'email': 'profile-pending@example.com',
            'x_account': '',
            'vrchat_user_id': '',
        })

        self.assertRedirects(response, reverse('account:settings'), fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertEqual(user.email, 'profile-change@example.com')
        self.assertTrue(EmailAddress.objects.filter(
            user=user,
            email='profile-pending@example.com',
            verified=False,
            primary=False,
        ).exists())

    def test_user_update_rejects_the_eleventh_email_change(self):
        """プロフィール経由の11回目の変更要求を送信前に拒否する。"""
        user = create_discord_linked_user(
            user_name='profile_limit_user',
            email='profile-limit@example.com',
            password='testpass123',
        )
        self.client.force_login(user)

        for index in range(EMAIL_CHANGE_LIMIT):
            response = self.client.post(reverse('account:user_update'), {
                'display_name': user.display_name,
                'user_name': user.user_name,
                'email': f'profile-limit-{index}@example.com',
                'x_account': '',
                'vrchat_user_id': '',
            })
            self.assertEqual(response.status_code, 302)

        response = self.client.post(reverse('account:user_update'), {
            'display_name': user.display_name,
            'user_name': user.user_name,
            'email': 'profile-limit-rejected@example.com',
            'x_account': '',
            'vrchat_user_id': '',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'メールアドレスの変更回数が上限に達しました')
        self.assertFalse(EmailAddress.objects.filter(
            user=user,
            email='profile-limit-rejected@example.com',
        ).exists())
        self.assertEqual(len(mail.outbox), EMAIL_CHANGE_LIMIT)

    def test_admin_rejects_the_eleventh_email_change(self):
        """管理画面もallauth共有バケットの11回目を送信前に拒否する。"""
        admin = User.objects.create_superuser(
            user_name='email_limit_admin',
            email='email-limit-admin@example.com',
            password='testpass123',
        )
        target = make_user(
            user_name='admin_limit_target',
            email='admin-limit-target@example.com',
            password='testpass123',
        )
        self.client.force_login(admin)
        url = reverse('admin:user_account_customuser_change', args=[target.pk])

        for index in range(EMAIL_CHANGE_LIMIT):
            response = self.client.post(url, self._admin_change_data(
                target,
                f'admin-limit-{index}@example.com',
            ))
            self.assertEqual(response.status_code, 302)

        response = self.client.post(url, self._admin_change_data(
            target,
            'admin-limit-rejected@example.com',
        ))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'メールアドレスの変更回数が上限に達しました')
        self.assertFalse(EmailAddress.objects.filter(
            user=target,
            email='admin-limit-rejected@example.com',
        ).exists())
        self.assertEqual(len(mail.outbox), EMAIL_CHANGE_LIMIT)

    def test_admin_rejects_email_owned_by_legacy_user_without_email_address(self):
        """EmailAddressがない既存ユーザーのemailも管理画面で奪えない。"""
        admin = User.objects.create_superuser(
            user_name='legacy_collision_admin',
            email='legacy-collision-admin@example.com',
            password='testpass123',
        )
        owner = make_user_without_email_address(
            user_name='legacy_email_owner',
            email='legacy-owner@example.com',
        )
        target = make_user(
            user_name='legacy_collision_target',
            email='legacy-collision-target@example.com',
        )
        self.client.force_login(admin)

        response = self.client.post(
            reverse('admin:user_account_customuser_change', args=[target.pk]),
            self._admin_change_data(target, owner.email.upper()),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'このメールアドレスは既に登録されています')
        target.refresh_from_db()
        self.assertEqual(target.email, 'legacy-collision-target@example.com')
        self.assertFalse(EmailAddress.objects.filter(
            user=target,
            email__iexact=owner.email,
        ).exists())
        self.assertEqual(len(mail.outbox), 0)

    @staticmethod
    def _admin_change_data(user, email):
        return {
            'email': email,
            'user_name': user.user_name,
            'display_name': user.display_name,
            'vrchat_user_id': '',
            'password': user.password,
            'is_active': 'on',
            'date_joined_0': user.date_joined.strftime('%Y-%m-%d'),
            'date_joined_1': user.date_joined.strftime('%H:%M:%S'),
            '_save': '保存',
        }


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    SOCIALACCOUNT_PROVIDERS=TEST_SOCIALACCOUNT_PROVIDERS,
)
class RegisterMailFailureTests(TestCase):
    """確認メール送信失敗時のローカル登録を確認する。"""

    def test_register_keeps_the_committed_unverified_identity_on_mail_failure(self):
        """送信に失敗してもログイン画面へ戻し、再送可能な状態を残す。"""
        with patch.object(
            CustomAccountAdapter,
            'send_mail',
            side_effect=RuntimeError('simulated mail failure'),
        ):
            response = self.client.post(reverse('account:register'), {
                'user_name': 'signup_mail_failure',
                'email': 'signup-mail-failure@example.com',
                'password1': 'testpass12345',
                'password2': 'testpass12345',
            })

        self.assertRedirects(response, reverse('account:login'), fetch_redirect_response=False)
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertIn(
            '登録は完了しました。確認メールの送信に失敗したため、ログイン画面から再送してください。',
            messages,
        )
        user = User.objects.get(user_name='signup_mail_failure')
        self.assertTrue(EmailAddress.objects.filter(
            user=user,
            email='signup-mail-failure@example.com',
            verified=False,
            primary=True,
        ).exists())

    @patch(
        'user_account.view_modules.session.complete_signup',
        side_effect=RuntimeError('simulated non-mail failure'),
    )
    def test_register_does_not_mask_non_mail_failures(self, _complete_signup):
        """メール送信以外の登録障害は送信失敗として握りつぶさない。"""
        with self.assertRaises(RuntimeError):
            self.client.post(reverse('account:register'), {
                'user_name': 'signup_non_mail_failure',
                'email': 'signup-non-mail-failure@example.com',
                'password1': 'testpass12345',
                'password2': 'testpass12345',
            })
