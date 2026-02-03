"""CommunitySettingsViewのテスト"""
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model

from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site

from community.models import Community, CommunityMember

CustomUser = get_user_model()

# テスト用のSOCIALACCOUNT_PROVIDERS設定（APPSなし）
TEST_SOCIALACCOUNT_PROVIDERS = {
    'discord': {
        'SCOPE': ['identify', 'email'],
    }
}


@override_settings(SOCIALACCOUNT_PROVIDERS=TEST_SOCIALACCOUNT_PROVIDERS)
class CommunitySettingsViewTest(TestCase):
    """集会設定ページのテスト"""

    def setUp(self):
        # Discord SocialAppを作成（テンプレートのprovider_login_urlタグに必要）
        site = Site.objects.get_current()
        social_app = SocialApp.objects.create(
            provider='discord',
            name='Discord',
            client_id='test-client-id',
            secret='test-secret'
        )
        social_app.sites.add(site)

        self.client = Client()

        # 主催者ユーザー
        self.owner_user = CustomUser.objects.create_user(
            email='owner@example.com',
            password='testpass123',
            user_name='主催者ユーザー'
        )

        # スタッフユーザー
        self.staff_user = CustomUser.objects.create_user(
            email='staff@example.com',
            password='testpass123',
            user_name='スタッフユーザー'
        )

        # 集会に所属していないユーザー
        self.other_user = CustomUser.objects.create_user(
            email='other@example.com',
            password='testpass123',
            user_name='その他ユーザー'
        )

        # テスト用集会
        self.community = Community.objects.create(
            name='テスト集会',
            status='approved',
            frequency='毎週',
            organizers='テスト主催者',
            weekdays=['Mon', 'Wed'],
        )

        # 主催者のメンバーシップ
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner_user,
            role=CommunityMember.Role.OWNER
        )

        # スタッフのメンバーシップ
        CommunityMember.objects.create(
            community=self.community,
            user=self.staff_user,
            role=CommunityMember.Role.STAFF
        )

    def test_owner_can_access_settings_page(self):
        """主催者は集会設定ページにアクセスできる"""
        self.client.login(username='主催者ユーザー', password='testpass123')
        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'テスト集会')
        self.assertContains(response, '集会設定')
        # 主催者はメンバー管理セクションが見える
        self.assertContains(response, 'メンバー管理')
        self.assertContains(response, 'メンバーを管理')

    def test_staff_can_access_settings_page(self):
        """スタッフも集会設定ページにアクセスできる"""
        self.client.login(username='スタッフユーザー', password='testpass123')
        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'テスト集会')
        self.assertContains(response, '集会設定')
        # スタッフはメンバー管理セクションが見えない
        self.assertNotContains(response, 'メンバーを管理')

    def test_anonymous_user_redirected_to_login(self):
        """未ログインユーザーはログインページにリダイレクトされる"""
        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/account/login/', response.url)

    def test_user_without_community_redirected(self):
        """集会を持っていないユーザーはリダイレクトされる"""
        self.client.login(username='その他ユーザー', password='testpass123')
        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('account:settings'))

    def test_settings_page_shows_external_links(self):
        """設定ページに外部連携リンクが表示される"""
        self.client.login(username='主催者ユーザー', password='testpass123')
        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 200)
        # カレンダー設定リンク
        self.assertContains(response, 'カレンダー設定')
        self.assertContains(response, reverse('community:calendar_update'))
        # Twitterテンプレートリンク
        self.assertContains(response, 'テンプレート管理')
        self.assertContains(response, reverse('twitter:template_list'))
        # API管理リンク
        self.assertContains(response, 'API管理')
        self.assertContains(response, reverse('account:api_key_list'))

    def test_settings_page_shows_weekdays(self):
        """設定ページに開催曜日が表示される"""
        self.client.login(username='主催者ユーザー', password='testpass123')
        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '月曜日')
        self.assertContains(response, '水曜日')

    def test_settings_page_shows_edit_link(self):
        """設定ページに集会編集リンクが表示される"""
        self.client.login(username='主催者ユーザー', password='testpass123')
        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '集会情報を編集')
        self.assertContains(response, reverse('community:update'))

    def test_active_community_from_session(self):
        """セッションに設定されたactive_community_idが使用される"""
        # 2つ目の集会を作成（ユニークな名前）
        second_community = Community.objects.create(
            name='セカンドコミュニティ',
            status='approved',
            frequency='隔週',
            organizers='テスト主催者',
        )
        CommunityMember.objects.create(
            community=second_community,
            user=self.owner_user,
            role=CommunityMember.Role.OWNER
        )

        self.client.login(username='主催者ユーザー', password='testpass123')

        # セッションに2番目の集会を設定
        session = self.client.session
        session['active_community_id'] = second_community.pk
        session.save()

        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 200)
        # タイトルに2番目の集会名が含まれていることを確認
        self.assertContains(response, '集会設定: セカンドコミュニティ')
        # 1番目の集会名がタイトルに含まれていないことを確認
        self.assertNotContains(response, '集会設定: テスト集会')




class CommunitySettingsCloseReopenTest(TestCase):
    """集会閉鎖/再開機能のテスト"""

    def setUp(self):
        self.client = Client()

        # 主催者ユーザー
        self.owner_user = CustomUser.objects.create_user(
            email='owner@example.com',
            password='testpass123',
            user_name='主催者ユーザー'
        )

        # スタッフユーザー
        self.staff_user = CustomUser.objects.create_user(
            email='staff@example.com',
            password='testpass123',
            user_name='スタッフユーザー'
        )

        # テスト用集会（活動中）
        self.community = Community.objects.create(
            name='テスト集会',
            status='approved',
            frequency='毎週',
            organizers='テスト主催者',
            weekdays=['Mon'],
        )

        # 主催者のメンバーシップ
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner_user,
            role=CommunityMember.Role.OWNER
        )

        # スタッフのメンバーシップ
        CommunityMember.objects.create(
            community=self.community,
            user=self.staff_user,
            role=CommunityMember.Role.STAFF
        )

    def test_owner_sees_close_button_for_active_community(self):
        """主催者は活動中の集会に対して閉鎖ボタンを見ることができる"""
        self.client.login(username='主催者ユーザー', password='testpass123')
        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 200)
        # 危険な操作セクションが表示される
        self.assertContains(response, '危険な操作')
        # 閉鎖ボタンが表示される
        self.assertContains(response, '集会を閉鎖する')
        self.assertContains(response, 'closeCommunityModal')
        # 再開ボタンは表示されない
        self.assertNotContains(response, '集会を再開する')

    def test_owner_sees_reopen_button_for_closed_community(self):
        """主催者は閉鎖された集会に対して再開ボタンを見ることができる"""
        from django.utils import timezone
        # 集会を閉鎖状態にする
        self.community.end_at = timezone.now().date()
        self.community.save()

        self.client.login(username='主催者ユーザー', password='testpass123')
        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 200)
        # 危険な操作セクションが表示される
        self.assertContains(response, '危険な操作')
        # 閉鎖済みアラートが表示される
        self.assertContains(response, 'に閉鎖されています')
        # 再開ボタンが表示される
        self.assertContains(response, '集会を再開する')
        self.assertContains(response, 'reopenCommunityModal')
        # 閉鎖ボタンは表示されない
        self.assertNotContains(response, '集会を閉鎖する')

    def test_staff_cannot_see_dangerous_operations(self):
        """スタッフは危険な操作セクションを見ることができない"""
        self.client.login(username='スタッフユーザー', password='testpass123')
        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 200)
        # 危険な操作セクションは表示されない
        self.assertNotContains(response, '危険な操作')
        self.assertNotContains(response, '集会を閉鎖する')

    def test_close_modal_exists(self):
        """閉鎖確認モーダルが存在する"""
        self.client.login(username='主催者ユーザー', password='testpass123')
        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 200)
        # モーダルの内容が含まれている
        self.assertContains(response, 'id="closeCommunityModal"')
        self.assertContains(response, '集会の閉鎖確認')
        self.assertContains(response, '閉鎖日以降のイベントは削除されます')
        self.assertContains(response, '閉鎖する')

    def test_reopen_modal_exists(self):
        """再開確認モーダルが存在する"""
        from django.utils import timezone
        self.community.end_at = timezone.now().date()
        self.community.save()

        self.client.login(username='主催者ユーザー', password='testpass123')
        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 200)
        # モーダルの内容が含まれている
        self.assertContains(response, 'id="reopenCommunityModal"')
        self.assertContains(response, '集会の再開確認')
        self.assertContains(response, '定期イベントを再開するには')
        self.assertContains(response, '再開する')


class WebhookSettingsTest(TestCase):
    """Webhook設定のテスト"""

    def setUp(self):
        self.client = Client()

        # 主催者ユーザー
        self.owner_user = CustomUser.objects.create_user(
            email='owner@example.com',
            password='testpass123',
            user_name='主催者ユーザー'
        )

        # スタッフユーザー
        self.staff_user = CustomUser.objects.create_user(
            email='staff@example.com',
            password='testpass123',
            user_name='スタッフユーザー'
        )

        # 権限のないユーザー
        self.other_user = CustomUser.objects.create_user(
            email='other@example.com',
            password='testpass123',
            user_name='その他ユーザー'
        )

        # テスト用集会
        self.community = Community.objects.create(
            name='テスト集会',
                        status='approved',
            frequency='毎週',
            organizers='テスト主催者',
            weekdays=['Mon'],
        )

        # 主催者のメンバーシップ
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner_user,
            role=CommunityMember.Role.OWNER
        )

        # スタッフのメンバーシップ
        CommunityMember.objects.create(
            community=self.community,
            user=self.staff_user,
            role=CommunityMember.Role.STAFF
        )

    def test_settings_page_shows_webhook_section(self):
        """設定ページにWebhookセクションが表示される"""
        self.client.login(username='主催者ユーザー', password='testpass123')
        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Discord通知')
        self.assertContains(response, '発表（LT）申請があった時にDiscordに通知を送信します')
        self.assertContains(response, 'notification_webhook_url')

    def test_update_webhook_url_success(self):
        """Webhook URLを正常に更新できる"""
        self.client.login(username='主催者ユーザー', password='testpass123')
        webhook_url = 'https://discord.com/api/webhooks/123456789/abcdef'

        response = self.client.post(
            reverse('community:update_webhook', kwargs={'pk': self.community.pk}),
            {'notification_webhook_url': webhook_url}
        )

        self.assertRedirects(response, reverse('community:settings'))
        self.community.refresh_from_db()
        self.assertEqual(self.community.notification_webhook_url, webhook_url)

    def test_update_webhook_url_invalid_format(self):
        """無効なWebhook URLの形式はエラーになる"""
        self.client.login(username='主催者ユーザー', password='testpass123')
        invalid_url = 'https://example.com/not-a-discord-webhook'

        response = self.client.post(
            reverse('community:update_webhook', kwargs={'pk': self.community.pk}),
            {'notification_webhook_url': invalid_url}
        )

        self.assertRedirects(response, reverse('community:settings'))
        self.community.refresh_from_db()
        # URLは更新されていない
        self.assertEqual(self.community.notification_webhook_url, '')

    def test_update_webhook_url_clear(self):
        """Webhook URLをクリアできる"""
        # 既にWebhook URLが設定されている状態
        self.community.notification_webhook_url = 'https://discord.com/api/webhooks/123/abc'
        self.community.save()

        self.client.login(username='主催者ユーザー', password='testpass123')
        response = self.client.post(
            reverse('community:update_webhook', kwargs={'pk': self.community.pk}),
            {'notification_webhook_url': ''}
        )

        self.assertRedirects(response, reverse('community:settings'))
        self.community.refresh_from_db()
        self.assertEqual(self.community.notification_webhook_url, '')

    def test_staff_can_update_webhook(self):
        """スタッフもWebhookを更新できる"""
        self.client.login(username='スタッフユーザー', password='testpass123')
        webhook_url = 'https://discord.com/api/webhooks/123456789/abcdef'

        response = self.client.post(
            reverse('community:update_webhook', kwargs={'pk': self.community.pk}),
            {'notification_webhook_url': webhook_url}
        )

        self.assertRedirects(response, reverse('community:settings'))
        self.community.refresh_from_db()
        self.assertEqual(self.community.notification_webhook_url, webhook_url)

    def test_unauthorized_user_cannot_update_webhook(self):
        """権限のないユーザーはWebhookを更新できない"""
        self.client.login(username='その他ユーザー', password='testpass123')

        response = self.client.post(
            reverse('community:update_webhook', kwargs={'pk': self.community.pk}),
            {'notification_webhook_url': 'https://discord.com/api/webhooks/123/abc'}
        )

        self.assertEqual(response.status_code, 403)

    def test_anonymous_user_cannot_update_webhook(self):
        """未ログインユーザーはWebhookを更新できない"""
        response = self.client.post(
            reverse('community:update_webhook', kwargs={'pk': self.community.pk}),
            {'notification_webhook_url': 'https://discord.com/api/webhooks/123/abc'}
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn('/account/login/', response.url)

    def test_test_button_shown_when_webhook_set(self):
        """Webhook URLが設定されている場合、テストボタンが表示される"""
        self.community.notification_webhook_url = 'https://discord.com/api/webhooks/123/abc'
        self.community.save()

        self.client.login(username='主催者ユーザー', password='testpass123')
        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'テスト送信')

    def test_test_button_not_shown_when_webhook_not_set(self):
        """Webhook URLが設定されていない場合、テストボタンは表示されない"""
        self.client.login(username='主催者ユーザー', password='testpass123')
        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'テスト送信')

    @patch('community.views.requests.post')
    def test_test_webhook_success(self, mock_post):
        """Webhookテスト送信が成功する"""
        self.community.notification_webhook_url = 'https://discord.com/api/webhooks/123/abc'
        self.community.save()

        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response

        self.client.login(username='主催者ユーザー', password='testpass123')
        response = self.client.post(
            reverse('community:test_webhook', kwargs={'pk': self.community.pk})
        )

        self.assertRedirects(response, reverse('community:settings'))
        mock_post.assert_called_once()
        # 送信されたJSONにテスト通知のメッセージが含まれていることを確認
        call_kwargs = mock_post.call_args[1]
        self.assertIn('テスト通知', call_kwargs['json']['content'])

    @patch('community.views.requests.post')
    def test_test_webhook_failure(self, mock_post):
        """Webhookテスト送信が失敗した場合のエラーハンドリング"""
        self.community.notification_webhook_url = 'https://discord.com/api/webhooks/123/abc'
        self.community.save()

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_post.return_value = mock_response

        self.client.login(username='主催者ユーザー', password='testpass123')
        response = self.client.post(
            reverse('community:test_webhook', kwargs={'pk': self.community.pk})
        )

        self.assertRedirects(response, reverse('community:settings'))

    def test_test_webhook_without_url(self):
        """Webhook URLが設定されていない場合はエラー"""
        self.client.login(username='主催者ユーザー', password='testpass123')
        response = self.client.post(
            reverse('community:test_webhook', kwargs={'pk': self.community.pk})
        )

        self.assertRedirects(response, reverse('community:settings'))

    def test_unauthorized_user_cannot_test_webhook(self):
        """権限のないユーザーはテスト送信できない"""
        self.community.notification_webhook_url = 'https://discord.com/api/webhooks/123/abc'
        self.community.save()

        self.client.login(username='その他ユーザー', password='testpass123')
        response = self.client.post(
            reverse('community:test_webhook', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 403)


class LTSettingsTest(TestCase):
    """LT申請設定のテスト"""

    def setUp(self):
        self.client = Client()

        # 主催者ユーザー
        self.owner_user = CustomUser.objects.create_user(
            email='owner@example.com',
            password='testpass123',
            user_name='主催者ユーザー'
        )

        # スタッフユーザー
        self.staff_user = CustomUser.objects.create_user(
            email='staff@example.com',
            password='testpass123',
            user_name='スタッフユーザー'
        )

        # 権限のないユーザー
        self.other_user = CustomUser.objects.create_user(
            email='other@example.com',
            password='testpass123',
            user_name='その他ユーザー'
        )

        # テスト用集会
        self.community = Community.objects.create(
            name='テスト集会',
                        status='approved',
            frequency='毎週',
            organizers='テスト主催者',
            weekdays=['Mon'],
        )

        # 主催者のメンバーシップ
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner_user,
            role=CommunityMember.Role.OWNER
        )

        # スタッフのメンバーシップ
        CommunityMember.objects.create(
            community=self.community,
            user=self.staff_user,
            role=CommunityMember.Role.STAFF
        )

    def test_settings_page_shows_lt_settings_section(self):
        """設定ページにLT申請設定セクションが表示される"""
        self.client.login(username='主催者ユーザー', password='testpass123')
        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'LT申請設定')
        self.assertContains(response, '追加情報テンプレート')
        self.assertContains(response, '最低文字数')

    def test_update_lt_settings_success(self):
        """LT申請設定を正常に更新できる"""
        self.client.login(username='主催者ユーザー', password='testpass123')
        template = '【発表概要】\n\n【対象者】'
        min_length = 50

        response = self.client.post(
            reverse('community:update_lt_settings', kwargs={'pk': self.community.pk}),
            {
                'lt_application_template': template,
                'lt_application_min_length': min_length,
            }
        )

        self.assertRedirects(response, reverse('community:settings'))
        self.community.refresh_from_db()
        self.assertEqual(self.community.lt_application_template, template)
        self.assertEqual(self.community.lt_application_min_length, min_length)

    def test_update_lt_settings_clear_template(self):
        """テンプレートをクリアできる"""
        # 既にテンプレートが設定されている状態
        self.community.lt_application_template = '【発表概要】'
        self.community.lt_application_min_length = 30
        self.community.save()

        self.client.login(username='主催者ユーザー', password='testpass123')
        response = self.client.post(
            reverse('community:update_lt_settings', kwargs={'pk': self.community.pk}),
            {
                'lt_application_template': '',
                'lt_application_min_length': 0,
            }
        )

        self.assertRedirects(response, reverse('community:settings'))
        self.community.refresh_from_db()
        self.assertEqual(self.community.lt_application_template, '')
        self.assertEqual(self.community.lt_application_min_length, 0)

    def test_staff_can_update_lt_settings(self):
        """スタッフもLT申請設定を更新できる"""
        self.client.login(username='スタッフユーザー', password='testpass123')
        template = '【テスト】'

        response = self.client.post(
            reverse('community:update_lt_settings', kwargs={'pk': self.community.pk}),
            {
                'lt_application_template': template,
                'lt_application_min_length': 10,
            }
        )

        self.assertRedirects(response, reverse('community:settings'))
        self.community.refresh_from_db()
        self.assertEqual(self.community.lt_application_template, template)

    def test_unauthorized_user_cannot_update_lt_settings(self):
        """権限のないユーザーはLT申請設定を更新できない"""
        self.client.login(username='その他ユーザー', password='testpass123')

        response = self.client.post(
            reverse('community:update_lt_settings', kwargs={'pk': self.community.pk}),
            {
                'lt_application_template': '【テスト】',
                'lt_application_min_length': 10,
            }
        )

        self.assertEqual(response.status_code, 403)

    def test_anonymous_user_cannot_update_lt_settings(self):
        """未ログインユーザーはLT申請設定を更新できない"""
        response = self.client.post(
            reverse('community:update_lt_settings', kwargs={'pk': self.community.pk}),
            {
                'lt_application_template': '【テスト】',
                'lt_application_min_length': 10,
            }
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn('/account/login/', response.url)

    def test_invalid_min_length_defaults_to_zero(self):
        """無効な最低文字数は0にデフォルトされる"""
        self.client.login(username='主催者ユーザー', password='testpass123')

        response = self.client.post(
            reverse('community:update_lt_settings', kwargs={'pk': self.community.pk}),
            {
                'lt_application_template': '【テスト】',
                'lt_application_min_length': 'invalid',  # 無効な値
            }
        )

        self.assertRedirects(response, reverse('community:settings'))
        self.community.refresh_from_db()
        self.assertEqual(self.community.lt_application_min_length, 0)

    def test_negative_min_length_defaults_to_zero(self):
        """負の最低文字数は0にデフォルトされる"""
        self.client.login(username='主催者ユーザー', password='testpass123')

        response = self.client.post(
            reverse('community:update_lt_settings', kwargs={'pk': self.community.pk}),
            {
                'lt_application_template': '【テスト】',
                'lt_application_min_length': -10,  # 負の値
            }
        )

        self.assertRedirects(response, reverse('community:settings'))
        self.community.refresh_from_db()
        self.assertEqual(self.community.lt_application_min_length, 0)

    def test_accepts_lt_application_toggle_on(self):
        """LT申請受付トグルをONにできる"""
        # 初期状態はOFF
        self.community.accepts_lt_application = False
        self.community.save()

        self.client.login(username='主催者ユーザー', password='testpass123')

        response = self.client.post(
            reverse('community:update_lt_settings', kwargs={'pk': self.community.pk}),
            {
                'accepts_lt_application': 'on',
                'lt_application_template': '【テスト】',
                'lt_application_min_length': 10,
                'default_lt_duration': 30,
            }
        )

        self.assertRedirects(response, reverse('community:settings'))
        self.community.refresh_from_db()
        self.assertTrue(self.community.accepts_lt_application)

    def test_accepts_lt_application_toggle_off(self):
        """LT申請受付トグルをOFFにできる"""
        # 初期状態はON
        self.community.accepts_lt_application = True
        self.community.save()

        self.client.login(username='主催者ユーザー', password='testpass123')

        # accepts_lt_applicationを送信しない（チェックボックスがOFFの場合）
        response = self.client.post(
            reverse('community:update_lt_settings', kwargs={'pk': self.community.pk}),
            {
                'lt_application_template': '【テスト】',
                'lt_application_min_length': 10,
                'default_lt_duration': 30,
            }
        )

        self.assertRedirects(response, reverse('community:settings'))
        self.community.refresh_from_db()
        self.assertFalse(self.community.accepts_lt_application)

    def test_settings_page_shows_lt_toggle(self):
        """設定ページにLT申請受付トグルが表示される"""
        self.client.login(username='主催者ユーザー', password='testpass123')
        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'LT申請を受け付ける')
        self.assertContains(response, 'id="accepts_lt_application"')

    def test_lt_detail_hidden_when_toggle_off(self):
        """トグルOFF時は詳細設定が非表示になる"""
        self.community.accepts_lt_application = False
        self.community.save()

        self.client.login(username='主催者ユーザー', password='testpass123')
        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 200)
        # 詳細設定のdivが非表示（display: none）になっている
        self.assertContains(response, 'id="lt-settings-detail" style="display: none;"')

    def test_lt_detail_visible_when_toggle_on(self):
        """トグルON時は詳細設定が表示される"""
        self.community.accepts_lt_application = True
        self.community.save()

        self.client.login(username='主催者ユーザー', password='testpass123')
        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 200)
        # 詳細設定のdivが非表示になっていない（display: none がない）
        self.assertContains(response, 'id="lt-settings-detail" style=""')

    def test_toggle_state_persists_after_page_reload(self):
        """トグル状態がページリロード後も維持される"""
        self.client.login(username='主催者ユーザー', password='testpass123')

        # トグルをONにして保存
        response = self.client.post(
            reverse('community:update_lt_settings', kwargs={'pk': self.community.pk}),
            {
                'accepts_lt_application': 'on',
                'lt_application_template': '【テスト】',
                'lt_application_min_length': 10,
                'default_lt_duration': 30,
            }
        )
        self.assertRedirects(response, reverse('community:settings'))

        # ページをリロード
        response = self.client.get(reverse('community:settings'))
        self.assertEqual(response.status_code, 200)
        # チェックボックスがチェック状態であることを確認
        self.assertContains(response, 'id="accepts_lt_application"')
        self.assertContains(response, 'checked')


class LTSettingsUpdateFormTest(TestCase):
    """集会更新フォームからLT受付設定が削除されたことのテスト"""

    def setUp(self):
        self.client = Client()

        # 主催者ユーザー
        self.owner_user = CustomUser.objects.create_user(
            email='owner@example.com',
            password='testpass123',
            user_name='主催者ユーザー'
        )

        # テスト用集会
        self.community = Community.objects.create(
            name='テスト集会',
            status='approved',
            frequency='毎週',
            organizers='テスト主催者',
            weekdays=['Mon'],
        )

        # 主催者のメンバーシップ
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner_user,
            role=CommunityMember.Role.OWNER
        )

    def test_update_page_does_not_have_accepts_lt_application(self):
        """集会更新ページにaccepts_lt_applicationフィールドがない"""
        self.client.login(username='主催者ユーザー', password='testpass123')

        # セッションにアクティブな集会を設定
        session = self.client.session
        session['active_community_id'] = self.community.pk
        session.save()

        response = self.client.get(reverse('community:update'))

        self.assertEqual(response.status_code, 200)
        # accepts_lt_applicationのラベルがページに含まれていないことを確認
        self.assertNotContains(response, 'LT申請を受け付ける')
        # フォームフィールドがないことを確認
        self.assertNotContains(response, 'id="id_accepts_lt_application"')
