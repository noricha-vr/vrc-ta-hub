"""認証ビューのテスト."""
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse

from community.models import Community

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


class SettingsViewTests(TestCase):
    """SettingsViewのテストクラス."""

    def setUp(self):
        """テスト用のデータを準備."""
        self.client = Client()
        self.settings_url = reverse('account:settings')
        self.test_user = User.objects.create_user(
            user_name='test_settings_user',
            email='test_settings@example.com',
            password='testpass123',
        )

    def test_settings_view_requires_login(self):
        """設定ページはログインが必要であること."""
        response = self.client.get(self.settings_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)

    def test_settings_view_renders_correctly(self):
        """ログイン状態で設定ページが正しくレンダリングされること."""
        self.client.login(username='test_settings_user', password='testpass123')
        response = self.client.get(self.settings_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'account/settings.html')

    def test_settings_view_without_community(self):
        """集会を持たないユーザーの設定ページが正しく表示されること."""
        self.client.login(username='test_settings_user', password='testpass123')
        response = self.client.get(self.settings_url)
        self.assertEqual(response.status_code, 200)
        # 集会登録リンクが表示されていること
        self.assertContains(response, '集会を登録')

    def test_settings_view_with_pending_community_shows_warning_with_discord_link(self):
        """承認待ち集会を持つユーザーに警告メッセージとDiscordリンクが表示されること."""
        # 承認待ち集会を作成
        Community.objects.create(
            custom_user=self.test_user,
            name='テスト集会',
            frequency='毎週',
            status='pending',
        )
        self.client.login(username='test_settings_user', password='testpass123')
        response = self.client.get(self.settings_url)
        self.assertEqual(response.status_code, 200)

        # メッセージを取得
        messages_list = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages_list), 1)
        message_text = str(messages_list[0])

        # 承認待ちメッセージが含まれていること
        self.assertIn('承認待ち', message_text)
        # Discordリンクが含まれていること
        self.assertIn('https://discord.gg/6jCkUUb9VN', message_text)
        self.assertIn('技術・学術系Hub', message_text)
        # リンクが適切な属性を持っていること
        self.assertIn('target="_blank"', message_text)
        self.assertIn('rel="noopener noreferrer"', message_text)

    def test_settings_view_with_approved_community_no_warning(self):
        """承認済み集会を持つユーザーには警告メッセージが表示されないこと."""
        # 承認済み集会を作成
        Community.objects.create(
            custom_user=self.test_user,
            name='テスト承認済み集会',
            frequency='毎週',
            status='approved',
        )
        self.client.login(username='test_settings_user', password='testpass123')
        response = self.client.get(self.settings_url)
        self.assertEqual(response.status_code, 200)

        # 警告メッセージがないこと
        messages_list = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages_list), 0)

    def test_settings_view_does_not_contain_other_section(self):
        """設定ページに「その他」セクションが表示されないこと."""
        # 承認待ち集会を作成（以前は「その他」セクションが表示される条件だった）
        Community.objects.create(
            custom_user=self.test_user,
            name='テスト集会',
            frequency='毎週',
            status='pending',
        )
        self.client.login(username='test_settings_user', password='testpass123')
        response = self.client.get(self.settings_url)
        self.assertEqual(response.status_code, 200)

        # 「その他」セクションが含まれていないこと
        self.assertNotContains(response, 'bi-three-dots')
        self.assertNotContains(response, 'headingOther')
        self.assertNotContains(response, 'collapseOther')


class RegisterViewTests(TestCase):
    """RegisterViewのテストクラス."""

    def setUp(self):
        """テスト用のデータを準備."""
        self.client = Client()
        self.register_url = reverse('account:register')

    def test_register_page_renders_correctly(self):
        """新規登録ページが正しくレンダリングされること."""
        response = self.client.get(self.register_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'account/register.html')

    def test_register_page_contains_discord_button(self):
        """新規登録ページにDiscordログインボタンが含まれていること."""
        response = self.client.get(self.register_url)
        self.assertContains(response, 'Discordで登録')
        self.assertContains(response, 'fab fa-discord')

    def test_register_page_does_not_contain_password_form(self):
        """新規登録ページにユーザー名/パスワードフォームが含まれていないこと."""
        response = self.client.get(self.register_url)
        self.assertNotContains(response, '<form method="post">')

    def test_register_page_contains_login_link(self):
        """新規登録ページにログインページへのリンクが含まれていること."""
        response = self.client.get(self.register_url)
        self.assertContains(response, '既にアカウントをお持ちの方は')
        self.assertContains(response, reverse('account:login'))

    def test_register_page_does_not_show_redirect_message(self):
        """新規登録ページにリダイレクトメッセージが表示されないこと."""
        response = self.client.get(self.register_url)
        # 以前の実装で表示されていたメッセージが含まれていないこと
        self.assertNotContains(response, '新規登録はDiscordアカウントで行ってください')

    def test_register_page_has_correct_header(self):
        """新規登録ページのヘッダーが「新規登録」であること."""
        response = self.client.get(self.register_url)
        self.assertContains(response, '<h4 class="mb-0">新規登録</h4>')

    def test_register_page_contains_terms_agreement(self):
        """新規登録ページに利用規約への同意文言が含まれていること."""
        response = self.client.get(self.register_url)
        self.assertContains(response, '登録することで')
        self.assertContains(response, '利用規約')
        self.assertContains(response, 'プライバシーポリシー')
        self.assertContains(response, '同意したものとみなされます')

    def test_register_page_terms_links_open_in_new_tab(self):
        """利用規約とプライバシーポリシーのリンクが新しいタブで開くこと."""
        response = self.client.get(self.register_url)
        content = response.content.decode('utf-8')
        # 利用規約リンクがtarget="_blank"を持つこと
        self.assertIn('href="/terms/"', content)
        self.assertIn('href="/privacy/"', content)
        # target="_blank"とrel="noopener noreferrer"が設定されていること
        self.assertIn('target="_blank"', content)
        self.assertIn('rel="noopener noreferrer"', content)
        self.assertIn('>利用規約</a>', content)
        self.assertIn('>プライバシーポリシー</a>', content)


class LoginPageRegisterLinkTests(TestCase):
    """ログインページの登録リンクテストクラス."""

    def setUp(self):
        """テスト用のデータを準備."""
        self.client = Client()
        self.login_url = reverse('account:login')

    def test_login_page_contains_register_link(self):
        """ログインページに新規登録ページへのリンクが含まれていること."""
        response = self.client.get(self.login_url)
        self.assertContains(response, 'アカウントをお持ちでない方は')
        self.assertContains(response, reverse('account:register'))
