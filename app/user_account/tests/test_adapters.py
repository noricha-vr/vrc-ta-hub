"""Discord OAuthアダプターのテスト."""
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from user_account.adapters import CustomSocialAccountAdapter

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
            discord_id='123456789'
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

    def test_pre_social_login_connects_existing_user(self):
        """既存ユーザーのdiscord_idと一致する場合、自動的に紐付けること."""
        request = self.factory.get('/accounts/discord/login/callback/')

        sociallogin = MagicMock()
        sociallogin.is_existing = False
        sociallogin.account.uid = '123456789'

        self.adapter.pre_social_login(request, sociallogin)

        sociallogin.connect.assert_called_once_with(request, self.existing_user)

    def test_pre_social_login_does_not_connect_when_no_match(self):
        """discord_idが一致しない場合、紐付けしないこと."""
        request = self.factory.get('/accounts/discord/login/callback/')

        sociallogin = MagicMock()
        sociallogin.is_existing = False
        sociallogin.account.uid = '999999999'

        self.adapter.pre_social_login(request, sociallogin)

        sociallogin.connect.assert_not_called()

    def test_pre_social_login_skips_when_already_existing(self):
        """既に紐付け済みの場合、何もしないこと."""
        request = self.factory.get('/accounts/discord/login/callback/')

        sociallogin = MagicMock()
        sociallogin.is_existing = True
        sociallogin.account.uid = '123456789'

        self.adapter.pre_social_login(request, sociallogin)

        sociallogin.connect.assert_not_called()

    def test_populate_user_sets_discord_fields(self):
        """新規ユーザー作成時にDiscordフィールドが設定されること."""
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

            self.assertEqual(result.discord_id, '987654321')
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

    def test_save_user_sets_discord_id_if_not_set(self):
        """discord_idが未設定の場合、保存時に設定すること."""
        request = self.factory.get('/accounts/discord/login/callback/')

        sociallogin = MagicMock()
        sociallogin.account.uid = '111222333'

        mock_user = MagicMock()
        mock_user.discord_id = None
        with patch('user_account.adapters.DefaultSocialAccountAdapter.save_user', return_value=mock_user):
            result = self.adapter.save_user(request, sociallogin)

            self.assertEqual(result.discord_id, '111222333')
            result.save.assert_called_once()


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
        user = User.objects.create_user(
            user_name='test_user',
            email='test@example.com',
            password='testpass123'
        )
        self.client.login(username='test_user', password='testpass123')

        response = self.client.get('/account/settings/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Discord連携')
