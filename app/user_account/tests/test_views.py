"""認証ビューのテスト."""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

User = get_user_model()


class CustomLoginViewTests(TestCase):
    """CustomLoginViewのテストクラス."""

    # 環境変数ベースの設定（APPS）を使用するため、
    # データベースへのSocialApp作成は不要。

    def setUp(self):
        """テスト用のデータを準備."""
        self.client = Client()
        self.login_url = reverse('account:login')
        self.test_user = User.objects.create_user(
            user_name='test_community',
            email='test@example.com',
            password='testpass123',
        )

    def test_login_page_renders_correctly(self):
        """ログインページが正しくレンダリングされること."""
        response = self.client.get(self.login_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'account/login.html')

    def test_login_page_contains_discord_button(self):
        """ログインページにDiscordログインボタンが含まれていること."""
        response = self.client.get(self.login_url)
        self.assertContains(response, 'Discordでログイン')
        self.assertContains(response, 'fab fa-discord')

    def test_login_page_contains_remember_checkbox(self):
        """ログインページに「ログインしたままにする」チェックボックスが含まれていること."""
        response = self.client.get(self.login_url)
        self.assertContains(response, 'ログインしたままにする')
        self.assertContains(response, 'form-check-input')

    def test_login_page_contains_password_reset_link(self):
        """ログインページに「パスワードをお忘れですか？」リンクが含まれていること."""
        response = self.client.get(self.login_url)
        self.assertContains(response, 'パスワードをお忘れですか？')

    def test_login_without_remember_sets_session_expiry_to_browser_close(self):
        """rememberなしでログインするとセッションがブラウザ終了時に切れること."""
        response = self.client.post(self.login_url, {
            'username': 'test_community',
            'password': 'testpass123',
            # remember フィールドを送信しない（チェックなし）
        })
        self.assertEqual(response.status_code, 302)  # リダイレクト
        # get_expire_at_browser_close()がTrueであることを確認
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_login_with_remember_keeps_session(self):
        """rememberありでログインするとセッションが維持されること."""
        response = self.client.post(self.login_url, {
            'username': 'test_community',
            'password': 'testpass123',
            'remember': 'on',  # チェックあり
        })
        self.assertEqual(response.status_code, 302)  # リダイレクト
        # get_expire_at_browser_close()がFalseであることを確認（セッションが維持される）
        self.assertFalse(self.client.session.get_expire_at_browser_close())

    def test_login_success_message(self):
        """ログイン成功時にメッセージが表示されること."""
        response = self.client.post(self.login_url, {
            'username': 'test_community',
            'password': 'testpass123',
        }, follow=True)
        self.assertContains(response, 'ログインしました')
