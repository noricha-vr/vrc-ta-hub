"""ログイン後の既定リダイレクト先を検証する。"""

from django.conf import settings
from django.test import Client, RequestFactory, TestCase, tag
from django.urls import reverse

from tests.factories import (
    make_community,
    make_discord_linked_user,
    make_user,
)
from user_account.adapters import CustomAccountAdapter


@tag('offline_external_api')
class LoginRedirectTests(TestCase):
    """ローカルログインとallauth adapterの既定遷移を検証する。"""

    def setUp(self):
        self.client = Client()
        self.factory = RequestFactory()
        self.adapter = CustomAccountAdapter()

    def test_local_login_with_membership_uses_existing_default(self):
        """集会所属ユーザーのローカルログインは既存の既定先へ遷移する。"""
        user = make_discord_linked_user(
            user_name='local_membership_user',
            email='local-membership@example.com',
        )
        make_community(name='ローカル遷移テスト集会', owner=user)

        response = self.client.post(reverse('account:login'), {
            'username': user.user_name,
            'password': 'testpass123',
        })

        self.assertRedirects(
            response,
            settings.LOGIN_REDIRECT_URL,
            fetch_redirect_response=False,
        )

    def test_account_adapter_without_membership_uses_my_presentations(self):
        """allauth adapterは集会未所属ユーザーを自分の発表へ遷移させる。"""
        request = self.factory.get('/accounts/discord/login/callback/')
        request.user = make_user(
            user_name='adapter_no_membership_user',
            email='adapter-no-membership@example.com',
        )

        self.assertEqual(
            self.adapter.get_login_redirect_url(request),
            reverse('event:my_presentations'),
        )

    def test_account_adapter_with_membership_uses_existing_default(self):
        """allauth adapterは集会所属ユーザーを既存の既定先へ遷移させる。"""
        request = self.factory.get('/accounts/discord/login/callback/')
        request.user = make_user(
            user_name='adapter_membership_user',
            email='adapter-membership@example.com',
        )
        make_community(name='adapter遷移テスト集会', owner=request.user)

        self.assertEqual(
            self.adapter.get_login_redirect_url(request),
            settings.LOGIN_REDIRECT_URL,
        )
