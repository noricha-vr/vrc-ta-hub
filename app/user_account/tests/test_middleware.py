"""Discord認証ミドルウェアのテスト."""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from allauth.socialaccount.models import SocialAccount

User = get_user_model()


@override_settings(DISCORD_AUTH_REQUIRED=True)
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
        response = self.client.get(reverse('account:logout'))
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
