"""Discord認証ミドルウェアのテスト."""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings, tag
from django.urls import reverse

from allauth.socialaccount.models import SocialAccount

User = get_user_model()


class DebugLoginSkipMiddlewareTests(TestCase):
    """DebugLoginSkipMiddlewareのテストクラス."""

    def setUp(self):
        """テスト用のクライアントを準備."""
        self.client = Client()

    @override_settings(
        DEBUG=True,
        DEBUG_LOGIN_SKIP=True,
        DEBUG_LOGIN_SKIP_USER_NAME='ai_agent',
        DEBUG_LOGIN_SKIP_USER_EMAIL='ai-agent@example.local',
        DISCORD_AUTH_REQUIRED=False,
    )
    def test_debug_login_skip_allows_protected_page(self):
        """DEBUG時の明示ONでは未ログインでも保護ページにアクセスできること."""
        response = self.client.get(reverse('account:settings'))

        self.assertEqual(response.status_code, 200)
        user = User.objects.get(user_name='ai_agent')
        self.assertEqual(response.wsgi_request.user, user)
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.has_usable_password())

    @override_settings(
        DEBUG=False,
        DEBUG_LOGIN_SKIP=True,
        DEBUG_LOGIN_SKIP_USER_NAME='ai_agent',
        DEBUG_LOGIN_SKIP_USER_EMAIL='ai-agent@example.local',
        DISCORD_AUTH_REQUIRED=False,
    )
    def test_debug_login_skip_is_disabled_when_debug_false(self):
        """DEBUG=Falseでは明示ONでもログインスキップされないこと."""
        response = self.client.get(reverse('account:settings'))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/account/login/', response['Location'])
        self.assertFalse(User.objects.filter(user_name='ai_agent').exists())

    @override_settings(
        DEBUG=True,
        DEBUG_LOGIN_SKIP=True,
        DEBUG_LOGIN_SKIP_USER_NAME='ai_agent',
        DEBUG_LOGIN_SKIP_USER_EMAIL='ai-agent@example.local',
        DISCORD_AUTH_REQUIRED=False,
    )
    def test_debug_login_skip_does_not_replace_authenticated_user(self):
        """ログイン済みユーザーはデバッグユーザーで上書きされないこと."""
        user = User.objects.create_user(
            user_name='real_user',
            email='real-user@example.local',
            password='testpass123',
        )
        self.client.login(username='real_user', password='testpass123')

        response = self.client.get(reverse('account:settings'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.wsgi_request.user, user)
        self.assertFalse(User.objects.filter(user_name='ai_agent').exists())


@override_settings(DISCORD_AUTH_REQUIRED=True)
@tag('external_api')
class DiscordAuthRequiredMiddlewareTests(TestCase):
    """DiscordAuthRequiredMiddlewareのテストクラス.

    テスト環境ではDISCORD_AUTH_REQUIRED=Falseに設定されているため、
    このテストクラスではoverride_settingsで明示的に有効化する。
    """

    def setUp(self):
        """テスト用のデータを準備."""
        self.client = Client()
        # Discord連携なしのユーザー
        self.user_without_discord = User.objects.create_user(
            user_name='test_no_discord',
            email='no_discord@example.com',
            password='testpass123',
        )
        # Discord連携ありのユーザー
        self.user_with_discord = User.objects.create_user(
            user_name='test_with_discord',
            email='with_discord@example.com',
            password='testpass123',
        )
        # Discord連携済みとしてSocialAccountを作成
        SocialAccount.objects.create(
            user=self.user_with_discord,
            provider='discord',
            uid='123456789',
        )

    def test_unauthenticated_user_not_redirected(self):
        """未認証ユーザーはリダイレクトされないこと."""
        response = self.client.get(reverse('account:settings'))
        # ログインページにリダイレクトされる（ミドルウェアではなくLoginRequiredMixinによる）
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)

    def test_user_with_discord_not_redirected(self):
        """Discord連携済みユーザーはリダイレクトされないこと."""
        self.client.login(username='test_with_discord', password='testpass123')
        response = self.client.get(reverse('account:settings'))
        self.assertEqual(response.status_code, 200)

    def test_user_without_discord_redirected_to_discord_required(self):
        """Discord未連携ユーザーは /account/discord-required/ にリダイレクトされること."""
        self.client.login(username='test_no_discord', password='testpass123')
        response = self.client.get(reverse('account:settings'))
        self.assertRedirects(
            response,
            reverse('account:discord_required'),
            fetch_redirect_response=False,
        )

    def test_exempt_path_logout_not_redirected(self):
        """除外パス /account/logout/ はリダイレクトされないこと."""
        self.client.login(username='test_no_discord', password='testpass123')
        # Django 5.0 で GET ログアウトは削除されたため POST を使う (Issue #387)
        response = self.client.post(reverse('account:logout'))
        # logout後のリダイレクト（discord_requiredではない）
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('discord-required', response.url)

    def test_exempt_path_discord_required_not_redirected(self):
        """除外パス /account/discord-required/ はリダイレクトされないこと（無限ループ防止）."""
        self.client.login(username='test_no_discord', password='testpass123')
        response = self.client.get(reverse('account:discord_required'))
        # 正常にページが表示される（リダイレクトループしない）
        self.assertEqual(response.status_code, 200)

    def test_exempt_path_admin_not_redirected(self):
        """除外パス /admin/ はリダイレクトされないこと."""
        self.client.login(username='test_no_discord', password='testpass123')
        response = self.client.get('/admin/')
        # adminログインページにリダイレクト or 表示される（discord_requiredではない）
        self.assertNotIn('discord-required', response.url if response.status_code == 302 else '')

    def test_exempt_path_static_not_redirected(self):
        """除外パス /static/ はリダイレクトされないこと."""
        self.client.login(username='test_no_discord', password='testpass123')
        # staticファイルへのアクセス（存在しなくても404が返るがdiscord-requiredにはリダイレクトされない）
        response = self.client.get('/static/test.css')
        self.assertNotEqual(response.status_code, 302)

    def test_discord_required_page_renders_correctly(self):
        """Discord連携必須ページが正しくレンダリングされること."""
        self.client.login(username='test_no_discord', password='testpass123')
        response = self.client.get(reverse('account:discord_required'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'account/discord_required.html')
        self.assertContains(response, 'Discord連携が必要です')
        self.assertContains(response, 'Discordで連携する')
        self.assertContains(response, 'ログアウト')
