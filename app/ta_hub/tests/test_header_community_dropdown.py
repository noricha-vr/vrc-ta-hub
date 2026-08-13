"""ヘッダーの集会ドロップダウン表示のテスト"""

import re

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from community.models import Community, CommunityMember

CustomUser = get_user_model()


class HeaderCommunityDropdownTest(TestCase):
    """ヘッダーの集会ドロップダウンメニューのテスト"""

    def setUp(self):
        self.client = Client()

        # テスト用ユーザーを作成
        self.user = CustomUser.objects.create_user(
            email='user@example.com',
            password='testpass123',
            user_name='テストユーザー'
        )
        self.user_no_community = CustomUser.objects.create_user(
            email='nocomm@example.com',
            password='testpass123',
            user_name='集会なしユーザー'
        )

        # テスト用集会を作成
        self.community1 = Community.objects.create(
            name='個人開発集会',
            status='approved',
            frequency='毎週'
        )
        self.community2 = Community.objects.create(
            name='技術共有会',
            status='approved',
            frequency='隔週'
        )

        # CommunityMemberを作成
        CommunityMember.objects.create(
            community=self.community1,
            user=self.user,
            role=CommunityMember.Role.OWNER
        )
        CommunityMember.objects.create(
            community=self.community2,
            user=self.user,
            role=CommunityMember.Role.STAFF
        )

    def test_anonymous_user_does_not_see_community_dropdown(self):
        """未認証ユーザーは集会ドロップダウンを見ない"""
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'マイ集会')
        self.assertNotContains(response, '個人開発集会')

    def test_user_without_communities_does_not_see_community_section(self):
        """集会未所属ユーザーはマイ集会セクションを見ない"""
        self.client.force_login(self.user_no_community)
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'マイ集会')
        self.assertContains(response, '自分の発表')
        self.assertContains(response, reverse('event:my_presentations'))

    def test_user_sees_all_communities_in_dropdown(self):
        """ユーザーは所属する全ての集会をドロップダウンで見る"""
        self.client.force_login(self.user)
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'マイ集会')
        self.assertContains(response, '個人開発集会')
        self.assertContains(response, '技術共有会')

    def test_my_presentations_link_is_not_duplicated_with_active_community(self):
        """集会所属ユーザーにも自分の発表導線を1件だけ表示する"""
        self.client.force_login(self.user)
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        presentations_url = reverse('event:my_presentations')
        self.assertContains(response, f'href="{presentations_url}"', count=1)
        self.assertContains(response, '自分の発表')

    def test_active_community_has_checkmark(self):
        """アクティブな集会にはチェックマークが表示される"""
        self.client.force_login(self.user)

        # セッションにactive_community_idを設定
        session = self.client.session
        session['active_community_id'] = self.community1.id
        session.save()

        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        # チェックマークアイコンが存在することを確認
        self.assertContains(response, 'bi-check-lg')

    def test_active_community_links_to_my_list(self):
        """アクティブな集会名はマイイベント一覧へのリンクになっている"""
        self.client.force_login(self.user)

        session = self.client.session
        session['active_community_id'] = self.community1.id
        session.save()

        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        my_list_url = reverse('event:my_list')
        links = re.findall(
            r'<a[^>]+href="{}"[^>]*>(.*?)</a>'.format(re.escape(my_list_url)),
            response.content.decode(),
            re.DOTALL,
        )
        self.assertEqual(len(links), 1)
        self.assertIn(self.community1.name, links[0])

    def test_inactive_community_has_circle_icon(self):
        """非アクティブな集会には丸アイコンが表示される"""
        self.client.force_login(self.user)

        # セッションにactive_community_idを設定
        session = self.client.session
        session['active_community_id'] = self.community1.id
        session.save()

        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        # 丸アイコンが存在することを確認（非アクティブ集会用）
        self.assertContains(response, 'bi-circle')

    def test_switch_form_exists_for_inactive_community(self):
        """非アクティブな集会には切り替えフォームがある"""
        self.client.force_login(self.user)

        # セッションにactive_community_idを設定
        session = self.client.session
        session['active_community_id'] = self.community1.id
        session.save()

        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        # 切り替えフォームにcommunity_idが含まれることを確認
        self.assertContains(response, f'name="community_id" value="{self.community2.id}"')

    def test_add_community_link_exists(self):
        """集会を追加リンクが存在する"""
        self.client.force_login(self.user)
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '集会を追加')
        self.assertContains(response, reverse('community:create'))

    def test_account_settings_link_exists(self):
        """アカウント設定リンクが存在する"""
        self.client.force_login(self.user)
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'アカウント設定')
        self.assertContains(response, reverse('account:settings'))

    def test_switch_community_redirects_to_my_list(self):
        """集会切り替え後はダッシュボードにリダイレクトする"""
        self.client.force_login(self.user)

        # セッションにactive_community_idを設定
        session = self.client.session
        session['active_community_id'] = self.community1.id
        session.save()

        # 集会2に切り替え
        response = self.client.post(
            reverse('community:switch'),
            {
                'community_id': self.community2.id,
                'redirect_to': reverse('event:my_list')
            }
        )

        # ダッシュボードにリダイレクトされることを確認
        self.assertRedirects(response, reverse('event:my_list'), fetch_redirect_response=False)

        # セッションが更新されていることを確認
        session = self.client.session
        self.assertEqual(session['active_community_id'], self.community2.id)
