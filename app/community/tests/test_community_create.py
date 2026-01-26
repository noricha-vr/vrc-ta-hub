"""集会登録機能のテスト."""
from unittest.mock import patch, MagicMock

from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.test import TestCase, Client, override_settings
from django.urls import reverse

from community.forms import CommunityCreateForm
from community.models import Community
from user_account.models import CustomUser


# テスト用のSOCIALACCOUNT_PROVIDERS設定（APPSなし）
# これにより、データベースのSocialAppのみが使用される
TEST_SOCIALACCOUNT_PROVIDERS = {
    'discord': {
        'SCOPE': ['identify', 'email'],
    }
}


class CommunityCreateFormTest(TestCase):
    """CommunityCreateFormのテスト."""

    def test_form_has_required_fields(self):
        """フォームに必須フィールドが含まれていることをテスト."""
        form = CommunityCreateForm()
        required_fields = [
            'name', 'start_time', 'duration', 'weekdays', 'frequency', 'organizers',
            'group_url', 'organizer_url', 'sns_url', 'discord', 'twitter_hashtag',
            'poster_image', 'allow_poster_repost', 'description', 'platform', 'tags'
        ]
        for field in required_fields:
            self.assertIn(field, form.fields)

    def test_poster_image_is_required(self):
        """poster_imageが必須であることをテスト."""
        form = CommunityCreateForm()
        self.assertTrue(form.fields['poster_image'].required)

    def test_tags_is_required(self):
        """tagsが必須であることをテスト."""
        form = CommunityCreateForm()
        self.assertTrue(form.fields['tags'].required)

    def test_tags_uses_form_tags_only(self):
        """tagsがFORM_TAGS（技術系・学術系）のみを使用していることをテスト."""
        from community.models import FORM_TAGS
        form = CommunityCreateForm()
        self.assertEqual(list(form.fields['tags'].choices), list(FORM_TAGS))


@override_settings(SOCIALACCOUNT_PROVIDERS=TEST_SOCIALACCOUNT_PROVIDERS)
class CommunityCreateViewTest(TestCase):
    """CommunityCreateViewのテスト."""

    def setUp(self):
        # Discord SocialAppを作成（テンプレートのprovider_login_urlタグに必要）
        # override_settingsでAPPS設定を無効化しているため、DBのSocialAppが使用される
        site = Site.objects.get_current()
        social_app = SocialApp.objects.create(
            provider='discord',
            name='Discord',
            client_id='test-client-id',
            secret='test-secret'
        )
        social_app.sites.add(site)

        self.client = Client()
        self.user = CustomUser.objects.create_user(
            user_name='テストユーザー',
            email='test@example.com',
            password='testpass123'
        )
        self.create_url = reverse('community:create')

    def test_unauthenticated_user_redirected_to_login(self):
        """未認証ユーザーがログインページにリダイレクトされることをテスト."""
        response = self.client.get(self.create_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/account/login/', response.url)

    def test_authenticated_user_can_access_create_page(self):
        """認証済みユーザーが集会登録ページにアクセスできることをテスト."""
        self.client.login(username='テストユーザー', password='testpass123')
        response = self.client.get(self.create_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'community/create.html')

    def test_user_with_existing_community_redirected_to_settings(self):
        """既に集会を持っているユーザーが設定ページにリダイレクトされることをテスト."""
        # ユーザーに集会を作成
        Community.objects.create(
            custom_user=self.user,
            name='既存集会',
            frequency='毎週',
            organizers='テスト主催者',
            description='テスト説明'
        )
        self.client.login(username='テストユーザー', password='testpass123')
        response = self.client.get(self.create_url)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('account:settings'))

    @patch('community.views.requests.post')
    def test_community_creation_via_post(self, mock_discord_post):
        """POSTリクエストで集会が作成されることをテスト.

        nameフィールドを含めて送信し、集会が正常に作成されることを確認する。
        """
        from django.core.files.uploadedfile import SimpleUploadedFile

        # Discord通知のモック
        mock_discord_post.return_value = MagicMock(status_code=200)

        self.client.login(username='テストユーザー', password='testpass123')

        # テスト用の画像ファイルを作成
        test_image = SimpleUploadedFile(
            name='test_poster.jpg',
            content=b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x00\x00\x00\x21\xf9\x04\x01\x0a\x00\x01\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x4c\x01\x00\x3b',
            content_type='image/gif'
        )

        # POSTデータ（nameを含む）
        post_data = {
            'name': 'テスト集会名',
            'start_time': '20:00',
            'duration': 60,
            'weekdays': ['Sat'],
            'frequency': '毎週',
            'organizers': 'テスト主催者',
            'group_url': '',
            'organizer_url': '',
            'sns_url': '',
            'discord': '',
            'twitter_hashtag': '',
            'poster_image': test_image,
            'allow_poster_repost': True,
            'description': 'テスト説明',
            'platform': 'All',
            'tags': ['tech'],
        }

        response = self.client.post(self.create_url, post_data)

        # 成功時はsettingsページにリダイレクト
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('account:settings'))

        # 集会が作成されていることを確認
        self.assertTrue(Community.objects.filter(custom_user=self.user).exists())

        # 集会のnameが送信した値になっていることを確認
        created_community = Community.objects.get(custom_user=self.user)
        self.assertEqual(created_community.name, 'テスト集会名')


@override_settings(SOCIALACCOUNT_PROVIDERS=TEST_SOCIALACCOUNT_PROVIDERS)
class RegisterRedirectViewTest(TestCase):
    """通常登録ページのリダイレクトテスト."""

    def setUp(self):
        # Discord SocialAppを作成（テンプレートのprovider_login_urlタグに必要）
        # override_settingsでAPPS設定を無効化しているため、DBのSocialAppが使用される
        site = Site.objects.get_current()
        social_app = SocialApp.objects.create(
            provider='discord',
            name='Discord',
            client_id='test-client-id',
            secret='test-secret'
        )
        social_app.sites.add(site)

        self.client = Client()
        self.register_url = reverse('account:register')

    def test_register_redirects_to_login(self):
        """通常登録ページがログインページにリダイレクトされることをテスト."""
        response = self.client.get(self.register_url)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('account:login'))


@override_settings(SOCIALACCOUNT_PROVIDERS=TEST_SOCIALACCOUNT_PROVIDERS)
class SettingsViewCommunityButtonTest(TestCase):
    """設定画面の集会登録ボタン表示テスト."""

    def setUp(self):
        # Discord SocialAppを作成（テンプレートのprovider_login_urlタグに必要）
        # override_settingsでAPPS設定を無効化しているため、DBのSocialAppが使用される
        site = Site.objects.get_current()
        social_app = SocialApp.objects.create(
            provider='discord',
            name='Discord',
            client_id='test-client-id',
            secret='test-secret'
        )
        social_app.sites.add(site)

        self.client = Client()
        self.user_with_community = CustomUser.objects.create_user(
            user_name='集会持ちユーザー',
            email='with@example.com',
            password='testpass123'
        )
        self.user_without_community = CustomUser.objects.create_user(
            user_name='集会なしユーザー',
            email='without@example.com',
            password='testpass123'
        )
        # 集会持ちユーザーに集会を作成
        Community.objects.create(
            custom_user=self.user_with_community,
            name='テスト集会',
            frequency='毎週',
            organizers='テスト主催者',
            description='テスト説明',
            status='approved'
        )
        self.settings_url = reverse('account:settings')

    def test_user_without_community_sees_create_button(self):
        """集会がないユーザーに「集会を登録」ボタンが表示されることをテスト."""
        self.client.login(username='集会なしユーザー', password='testpass123')
        response = self.client.get(self.settings_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '集会を登録')
        self.assertContains(response, reverse('community:create'))

    def test_user_with_community_does_not_see_create_button(self):
        """集会があるユーザーには「集会を登録」ボタンが表示されないことをテスト."""
        self.client.login(username='集会持ちユーザー', password='testpass123')
        response = self.client.get(self.settings_url)
        self.assertEqual(response.status_code, 200)
        # 集会登録ボタンが表示されない
        self.assertNotContains(response, reverse('community:create'))
        # 集会編集リンクが表示される
        self.assertContains(response, '集会情報を編集')


@override_settings(SOCIALACCOUNT_PROVIDERS=TEST_SOCIALACCOUNT_PROVIDERS)
class SettingsViewEmailAlertTest(TestCase):
    """設定画面のメールアドレス未設定アラート表示テスト."""

    def setUp(self):
        # Discord SocialAppを作成（テンプレートのprovider_login_urlタグに必要）
        # override_settingsでAPPS設定を無効化しているため、DBのSocialAppが使用される
        site = Site.objects.get_current()
        social_app = SocialApp.objects.create(
            provider='discord',
            name='Discord',
            client_id='test-client-id',
            secret='test-secret'
        )
        social_app.sites.add(site)

        self.client = Client()
        self.user_with_email = CustomUser.objects.create_user(
            user_name='メール有りユーザー',
            email='with@example.com',
            password='testpass123'
        )
        self.user_without_email = CustomUser.objects.create_user(
            user_name='メール無しユーザー',
            email='',
            password='testpass123'
        )
        self.settings_url = reverse('account:settings')

    def test_user_without_email_sees_warning_alert(self):
        """メールアドレス未設定のユーザーに警告アラートが表示されることをテスト."""
        self.client.login(username='メール無しユーザー', password='testpass123')
        response = self.client.get(self.settings_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'メールアドレスが未設定です')

    def test_user_with_email_does_not_see_warning_alert(self):
        """メールアドレス設定済みのユーザーには警告アラートが表示されないことをテスト."""
        self.client.login(username='メール有りユーザー', password='testpass123')
        response = self.client.get(self.settings_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'メールアドレスが未設定です')
