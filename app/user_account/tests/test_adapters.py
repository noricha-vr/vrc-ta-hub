"""Discord OAuthアダプターのテスト."""
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from allauth.socialaccount.models import SocialAccount
from user_account.adapters import CustomSocialAccountAdapter
from user_account.tests.utils import create_discord_linked_user

User = get_user_model()


class CustomSocialAccountAdapterTests(TestCase):
    """CustomSocialAccountAdapterのテストクラス."""

    def setUp(self):
        """テスト用のデータを準備."""
        self.factory = RequestFactory()
        self.adapter = CustomSocialAccountAdapter()
        self.existing_user = User.objects.create_user(
            user_name='existing_user',
            email='existing@example.com',
            password='testpass123',
        )

    def test_is_auto_signup_allowed_returns_true_when_email_exists(self):
        """メールアドレスがある場合、自動サインアップを許可すること."""
        request = self.factory.get('/accounts/discord/login/callback/')

        sociallogin = MagicMock()
        sociallogin.account.extra_data = {'email': 'test@example.com'}

        result = self.adapter.is_auto_signup_allowed(request, sociallogin)

        self.assertTrue(result)

    def test_is_auto_signup_allowed_returns_false_when_email_missing(self):
        """メールアドレスがない場合、自動サインアップを許可しないこと（フォーム表示）."""
        request = self.factory.get('/accounts/discord/login/callback/')

        sociallogin = MagicMock()
        sociallogin.account.extra_data = {'email': ''}

        result = self.adapter.is_auto_signup_allowed(request, sociallogin)

        self.assertFalse(result)

    def test_is_auto_signup_allowed_returns_false_when_email_key_missing(self):
        """extra_dataにemailキーがない場合、自動サインアップを許可しないこと（フォーム表示）."""
        request = self.factory.get('/accounts/discord/login/callback/')

        sociallogin = MagicMock()
        sociallogin.account.extra_data = {}

        result = self.adapter.is_auto_signup_allowed(request, sociallogin)

        self.assertFalse(result)

    def test_pre_social_login_skips_when_already_existing(self):
        """既に紐付け済みの場合、何もしないこと."""
        request = self.factory.get('/accounts/discord/login/callback/')

        sociallogin = MagicMock()
        sociallogin.is_existing = True
        sociallogin.account.uid = '123456789'

        self.adapter.pre_social_login(request, sociallogin)

        sociallogin.connect.assert_not_called()

    def test_populate_user_sets_user_fields(self):
        """新規ユーザー作成時にユーザーフィールドが設定されること."""
        request = self.factory.get('/accounts/discord/login/callback/')

        sociallogin = MagicMock()
        sociallogin.account.uid = '987654321'

        data = {
            'username': 'discord_user',
            'email': 'discord@example.com',
        }

        mock_user = MagicMock()
        with patch('user_account.adapters.DefaultSocialAccountAdapter.populate_user', return_value=mock_user):
            result = self.adapter.populate_user(request, sociallogin, data)

            self.assertEqual(result.user_name, 'discord_user')
            self.assertEqual(result.email, 'discord@example.com')

    def test_populate_user_generates_username_when_not_provided(self):
        """Discordユーザー名がない場合、discord_idからユーザー名を生成すること."""
        request = self.factory.get('/accounts/discord/login/callback/')

        sociallogin = MagicMock()
        sociallogin.account.uid = '987654321'

        data = {
            'username': '',
            'email': '',
        }

        mock_user = MagicMock()
        with patch('user_account.adapters.DefaultSocialAccountAdapter.populate_user', return_value=mock_user):
            result = self.adapter.populate_user(request, sociallogin, data)

            self.assertEqual(result.user_name, 'discord_987654321')
            # メールが空の場合は空文字のまま（フォームで入力を要求）
            self.assertEqual(result.email, '')

    def test_populate_user_keeps_empty_email_when_not_provided(self):
        """メールが取得できない場合、メールは空のままであること（フォームで入力を要求）."""
        request = self.factory.get('/accounts/discord/login/callback/')

        sociallogin = MagicMock()
        sociallogin.account.uid = '555666777'

        data = {
            'username': 'discord_user_no_email',
            'email': '',  # メールなし
        }

        mock_user = MagicMock()
        with patch('user_account.adapters.DefaultSocialAccountAdapter.populate_user', return_value=mock_user):
            result = self.adapter.populate_user(request, sociallogin, data)

            self.assertEqual(result.email, '')

    def test_populate_user_uses_real_email_when_provided(self):
        """メールが提供された場合、プレースホルダーではなく実際のメールを使用すること."""
        request = self.factory.get('/accounts/discord/login/callback/')

        sociallogin = MagicMock()
        sociallogin.account.uid = '888999000'

        data = {
            'username': 'discord_user_with_email',
            'email': 'real@example.com',
        }

        mock_user = MagicMock()
        with patch('user_account.adapters.DefaultSocialAccountAdapter.populate_user', return_value=mock_user):
            result = self.adapter.populate_user(request, sociallogin, data)

            self.assertEqual(result.email, 'real@example.com')

    def test_populate_user_handles_username_collision(self):
        """ユーザー名が既存ユーザーと衝突する場合、ユニーク化すること."""
        # 既存ユーザーを作成（衝突テスト用）
        User.objects.create_user(
            user_name='collision_user',
            email='collision@example.com',
            password='testpass123'
        )

        request = self.factory.get('/accounts/discord/login/callback/')

        sociallogin = MagicMock()
        sociallogin.account.uid = '1234567890abcdef'

        data = {
            'username': 'collision_user',  # 既存ユーザーと同じ名前
            'email': 'new@example.com',
        }

        mock_user = MagicMock()
        with patch('user_account.adapters.DefaultSocialAccountAdapter.populate_user', return_value=mock_user):
            result = self.adapter.populate_user(request, sociallogin, data)

            # discord_idの先頭8文字が付加されていること
            self.assertEqual(result.user_name, 'collision_user_12345678')

    def test_populate_user_handles_multiple_username_collisions(self):
        """複数回のユーザー名衝突に対応すること."""
        discord_id = '1234567890abcdef'

        # 既存ユーザーを複数作成（複数回衝突テスト用）
        User.objects.create_user(
            user_name='multi_collision',
            email='multi1@example.com',
            password='testpass123'
        )
        User.objects.create_user(
            user_name='multi_collision_12345678',  # discord_id先頭8文字と同じ
            email='multi2@example.com',
            password='testpass123'
        )

        request = self.factory.get('/accounts/discord/login/callback/')

        sociallogin = MagicMock()
        sociallogin.account.uid = discord_id

        data = {
            'username': 'multi_collision',
            'email': 'new@example.com',
        }

        mock_user = MagicMock()
        with patch('user_account.adapters.DefaultSocialAccountAdapter.populate_user', return_value=mock_user):
            result = self.adapter.populate_user(request, sociallogin, data)

            # 2回目の衝突なのでカウンター（2）が付加されていること
            self.assertEqual(result.user_name, 'multi_collision_2')

    def test_save_user_sets_user_name_from_form(self):
        """フォームからuser_nameを取得して保存すること."""
        request = self.factory.get('/accounts/discord/login/callback/')

        sociallogin = MagicMock()
        sociallogin.account.uid = '444555666'

        # フォームのモック
        mock_form = MagicMock()
        mock_form.cleaned_data = {'user_name': 'form_username', 'email': 'test@example.com'}

        mock_user = MagicMock()
        with patch('user_account.adapters.DefaultSocialAccountAdapter.save_user', return_value=mock_user):
            result = self.adapter.save_user(request, sociallogin, form=mock_form)

            # user_nameがフォームの値に設定されていること
            self.assertEqual(result.user_name, 'form_username')
            # saveが呼ばれていること
            result.save.assert_called_once()

    def test_save_user_without_form_does_not_save(self):
        """フォームがない場合、saveが呼ばれないこと."""
        request = self.factory.get('/accounts/discord/login/callback/')

        sociallogin = MagicMock()
        sociallogin.account.uid = '000111222'

        mock_user = MagicMock()
        mock_user.user_name = 'original_name'
        with patch('user_account.adapters.DefaultSocialAccountAdapter.save_user', return_value=mock_user):
            result = self.adapter.save_user(request, sociallogin, form=None)

            # user_nameは変更されないこと
            self.assertEqual(result.user_name, 'original_name')
            # saveは呼ばれないこと（フォームがなければ更新不要）
            result.save.assert_not_called()

    def test_get_connect_redirect_url_returns_settings_page(self):
        """連携後のリダイレクト先が設定ページであること."""
        request = self.factory.get('/accounts/discord/login/callback/')
        request.user = self.existing_user

        socialaccount = MagicMock()

        result = self.adapter.get_connect_redirect_url(request, socialaccount)

        self.assertEqual(result, '/account/settings/')


class DiscordLoginIntegrationTests(TestCase):
    """Discord OAuth統合テスト."""

    @classmethod
    def setUpTestData(cls):
        """テスト用のSocialAppを設定."""
        # テスト環境では環境変数ベースの設定（APPS）が有効な場合があるため、
        # データベースのSocialAppは作成しない。
        # 代わりにテスト設定でAPPSを使用する。
        pass

    def test_login_page_shows_discord_button(self):
        """ログインページにDiscordログインボタンが表示されること."""
        response = self.client.get('/account/login/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Discordでログイン')
        self.assertContains(response, 'fa-discord')

    def test_settings_page_shows_discord_connection_status(self):
        """設定ページにDiscord連携状態が表示されること."""
        # Discord連携済みユーザーを作成（ミドルウェアでリダイレクトされないため）
        create_discord_linked_user(
            user_name='test_user',
            email='test@example.com',
            password='testpass123',
        )
        self.client.login(username='test_user', password='testpass123')

        response = self.client.get('/account/settings/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Discord連携')

    def test_connections_page_does_not_show_delete_button(self):
        """連携管理ページに削除ボタンが表示されないこと."""
        # Discord連携済みユーザーを作成（ミドルウェアでリダイレクトされないため）
        create_discord_linked_user(
            user_name='test_user_conn',
            email='test_conn@example.com',
            password='testpass123',
        )
        self.client.login(username='test_user_conn', password='testpass123')

        response = self.client.get('/accounts/3rdparty/')

        self.assertEqual(response.status_code, 200)
        # 削除ボタン/フォームが表示されないこと
        self.assertNotContains(response, 'type="submit"')
        self.assertNotContains(response, 'disconnect')
        # 設定ページへのリンクがあること
        self.assertContains(response, '設定ページに戻る')
        self.assertContains(response, '/account/settings/')
