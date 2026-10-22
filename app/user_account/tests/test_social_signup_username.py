"""Discord自動サインアップで user_name がそのまま保存されることを検証する.

user_name は一意制約を持たないため、既存ユーザーと同名の Discord ユーザー名でも
別名に置換されず保存される必要がある（allauth の clean_username による
「重複＝エラー」判定を CustomAccountAdapter で無効化している）。
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase, tag

from allauth.account.models import EmailAddress
from allauth.core.context import request_context
from allauth.socialaccount.internal.flows.signup import process_signup
from allauth.socialaccount.models import SocialAccount, SocialLogin

from tests.factories import make_user

User = get_user_model()


@tag('offline_external_api')
class DiscordAutoSignupUserNameTests(TestCase):
    """Discord自動サインアップ経由で保存される user_name を検証する."""

    def setUp(self):
        self.factory = RequestFactory()

    def _auto_signup(self, discord_username, email, discord_uid):
        """allauthの自動サインアップフローを実行し、保存されたユーザーを返す."""
        request = self.factory.get('/accounts/discord/login/')
        request.user = AnonymousUser()
        SessionMiddleware(lambda request: None).process_request(request)
        request.session.save()
        MessageMiddleware(lambda request: None).process_request(request)

        user = User(
            user_name=discord_username,
            display_name=discord_username,
            email=email,
        )
        sociallogin = SocialLogin(
            user=user,
            account=SocialAccount(
                provider='discord',
                uid=discord_uid,
                extra_data={
                    'email': email,
                    'verified': True,
                    'username': discord_username,
                },
            ),
            email_addresses=[EmailAddress(email=email, verified=True, primary=True)],
        )
        with request_context(request):
            sociallogin.state = SocialLogin.state_from_request(request)
            process_signup(request, sociallogin)

        return User.objects.get(email=email)

    def test_duplicated_discord_username_is_saved_as_is(self):
        """既存ユーザーと同名でも Discord ユーザー名がそのまま保存されること."""
        make_user(user_name='probe_name', email='existing-probe@example.com')

        created = self._auto_signup(
            discord_username='probe_name',
            email='flow-new@example.com',
            discord_uid='duplicate-name-uid',
        )

        self.assertEqual(created.user_name, 'probe_name')
        self.assertEqual(User.objects.filter(user_name='probe_name').count(), 2)

    def test_case_insensitive_duplicate_username_is_saved_as_is(self):
        """大文字小文字違いの同名でも Discord ユーザー名がそのまま保存されること."""
        make_user(user_name='probe_name', email='existing-lower@example.com')

        created = self._auto_signup(
            discord_username='PROBE_NAME',
            email='flow-upper@example.com',
            discord_uid='case-insensitive-uid',
        )

        self.assertEqual(created.user_name, 'PROBE_NAME')

    def test_unique_discord_username_is_saved_as_is(self):
        """検証済みDiscordメールは mandatory でも確認済みとして保存されること."""
        created = self._auto_signup(
            discord_username='solo_name',
            email='flow-solo@example.com',
            discord_uid='unique-name-uid',
        )

        self.assertEqual(created.user_name, 'solo_name')
        self.assertTrue(EmailAddress.objects.filter(
            user=created,
            email='flow-solo@example.com',
            verified=True,
            primary=True,
        ).exists())
