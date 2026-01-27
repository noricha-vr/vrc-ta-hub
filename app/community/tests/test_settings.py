"""CommunitySettingsViewのテスト"""
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from community.models import Community, CommunityMember

CustomUser = get_user_model()


class CommunitySettingsViewTest(TestCase):
    """集会設定ページのテスト"""

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

        # 集会に所属していないユーザー
        self.other_user = CustomUser.objects.create_user(
            email='other@example.com',
            password='testpass123',
            user_name='その他ユーザー'
        )

        # テスト用集会
        self.community = Community.objects.create(
            name='テスト集会',
            custom_user=self.owner_user,
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
            custom_user=self.owner_user,
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


class CommunitySettingsViewBackwardCompatibilityTest(TestCase):
    """CommunitySettingsViewの後方互換性テスト"""

    def setUp(self):
        self.client = Client()

        # レガシーオーナー（CommunityMemberなしで集会を持つ）
        self.legacy_owner = CustomUser.objects.create_user(
            email='legacy@example.com',
            password='testpass123',
            user_name='レガシーオーナー'
        )

        # レガシー集会（CommunityMemberなし）
        self.legacy_community = Community.objects.create(
            name='レガシー集会',
            custom_user=self.legacy_owner,
            status='approved',
            frequency='毎週',
            organizers='レガシー主催者',
        )
        # 意図的にCommunityMemberを作成しない

    def test_legacy_owner_can_access_settings(self):
        """CommunityMember未作成でもcustom_userは設定ページにアクセスできる"""
        self.assertFalse(
            CommunityMember.objects.filter(community=self.legacy_community).exists()
        )

        self.client.login(username='レガシーオーナー', password='testpass123')
        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'レガシー集会')

    def test_legacy_owner_is_treated_as_owner(self):
        """custom_userは主催者として扱われ、メンバー管理セクションが見える"""
        self.client.login(username='レガシーオーナー', password='testpass123')
        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 200)
        # 主催者としてメンバー管理セクションが表示される
        self.assertContains(response, 'メンバー管理')
        self.assertContains(response, 'メンバーを管理')
