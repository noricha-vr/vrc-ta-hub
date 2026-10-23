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
        self.assertNotContains(response, 'class="dropdown-menu dropdown-menu-end account-menu"')
        self.assertNotContains(response, '個人開発集会')

    def test_user_without_communities_does_not_see_community_section(self):
        """集会未所属ユーザーのメニューには集会リンクが出ない"""
        self.client.force_login(self.user_no_community)
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(self._find_my_list_links(response))

    def test_menu_has_no_section_header_or_redundant_links(self):
        """集会一覧の見出し・自分の発表・集会を追加・アカウント設定の項目はメニューに出さない"""
        self.client.force_login(self.user)
        session = self.client.session
        session['active_community_id'] = self.community1.id
        session.save()

        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        menu = self._account_menu_html(response)
        self.assertNotIn('集会一覧', menu)
        self.assertNotIn(reverse('event:detail_history') + '?mine=1', menu)
        self.assertNotIn(reverse('community:create'), menu)
        self.assertNotIn('bi-gear', menu)

    def test_identity_links_to_account_settings(self):
        """メニュー上部の表示名とユーザー名はアカウント設定へのリンクになっている"""
        self.user.display_name = '表示名さん'
        self.user.save(update_fields=['display_name'])
        self.client.force_login(self.user)

        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        menu = self._account_menu_html(response)
        identity = re.search(
            r'<a class="dropdown-item account-menu-identity" href="([^"]+)"[^>]*>(.*?)</a>', menu, re.DOTALL
        )
        self.assertIsNotNone(identity, 'アカウント設定への identity リンクが見つからない')
        self.assertEqual(identity.group(1), reverse('account:settings'))
        self.assertIn('表示名さん', identity.group(2))
        self.assertIn('テストユーザー', identity.group(2))

    def test_long_community_name_is_not_truncated(self):
        """集会名は省略せずに全文を表示する"""
        long_name = 'VRChat 技術学術ハッカソン準備会'
        self.community2.name = long_name
        self.community2.save(update_fields=['name'])
        self.client.force_login(self.user)
        session = self.client.session
        session['active_community_id'] = self.community1.id
        session.save()

        response = self.client.get(reverse('ta_hub:index'))

        self.assertContains(response, long_name)

    def test_user_sees_all_communities_in_dropdown(self):
        """ユーザーは所属する全ての集会をドロップダウンで見る"""
        self.client.force_login(self.user)
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '個人開発集会')
        self.assertContains(response, '技術共有会')

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

    def _account_menu_html(self, response):
        """ヘッダーのアカウントメニュー（ul.account-menu）部分の HTML を返す"""
        match = re.search(
            r'<ul class="dropdown-menu dropdown-menu-end account-menu">(.*?)</ul>',
            response.content.decode(),
            re.DOTALL,
        )
        self.assertIsNotNone(match, 'アカウントメニューが見つからない')
        return match.group(1)

    def _find_my_list_links(self, response):
        """マイイベント一覧への集会指定リンクを (href, 内側HTML) で列挙する"""
        my_list_url = reverse('event:my_list')
        pattern = r'<a([^>]+href="{}\?community=\d+"[^>]*)>(.*?)</a>'.format(
            re.escape(my_list_url)
        )
        return re.findall(pattern, response.content.decode(), re.DOTALL)

    def test_active_community_links_to_my_list_with_community_param(self):
        """アクティブな集会名は集会IDを固定したマイイベント一覧リンクになっている"""
        self.client.force_login(self.user)

        session = self.client.session
        session['active_community_id'] = self.community1.id
        session.save()

        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        my_list_url = reverse('event:my_list')
        expected_href = f'{my_list_url}?community={self.community1.id}'
        self.assertTrue(
            any(
                expected_href in attrs and self.community1.name in inner
                for attrs, inner in self._find_my_list_links(response)
            ),
            'アクティブ集会名を含む my_list リンクが見つからない',
        )

    def test_active_community_link_marked_as_current(self):
        """アクティブな集会リンクは Bootstrap の選択状態で示される"""
        self.client.force_login(self.user)

        session = self.client.session
        session['active_community_id'] = self.community1.id
        session.save()

        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        expected_href = '{}?community={}'.format(
            reverse('event:my_list'), self.community1.id
        )
        active_links = [
            attrs
            for attrs, inner in self._find_my_list_links(response)
            if self.community1.name in inner
        ]
        self.assertTrue(active_links, 'アクティブ集会のリンクが見つからない')
        self.assertIn(expected_href, active_links[0])
        self.assertIn('aria-current="true"', active_links[0])
        # 部分一致だと "dropdown-item" 等の別語に含まれる 'active' でも通るため class 属性値で照合する
        self.assertIn('class="dropdown-item active"', active_links[0])

    def test_inactive_community_has_no_circle_icon(self):
        """非アクティブな集会には丸アイコンを出さない（選択中だけをチェックで示す）"""
        self.client.force_login(self.user)

        # セッションにactive_community_idを設定
        session = self.client.session
        session['active_community_id'] = self.community1.id
        session.save()

        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('bi-circle', self._account_menu_html(response))

    def test_inactive_community_links_to_my_list_with_community_param(self):
        """非アクティブな集会は集会IDを固定したGETリンクで切り替えられる"""
        self.client.force_login(self.user)

        # セッションにactive_community_idを設定
        session = self.client.session
        session['active_community_id'] = self.community1.id
        session.save()

        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        expected_href = '{}?community={}'.format(
            reverse('event:my_list'), self.community2.id
        )
        self.assertTrue(
            any(
                expected_href in attrs and self.community2.name in inner
                for attrs, inner in self._find_my_list_links(response)
            ),
            '非アクティブ集会名を含む my_list リンクが見つからない',
        )

    def test_inactive_community_link_switches_active_community(self):
        """ドロップダウンに描画されたリンクをそのまま辿るとアクティブな集会が切り替わる"""
        self.client.force_login(self.user)

        session = self.client.session
        session['active_community_id'] = self.community1.id
        session.save()

        header_response = self.client.get(reverse('ta_hub:index'))
        # 描画済み href をそのまま辿ることで、テンプレートとビューの結線ずれを検知する
        hrefs = [
            re.search(r'href="([^"]+)"', attrs).group(1)
            for attrs, inner in self._find_my_list_links(header_response)
            if self.community2.name in inner
        ]
        self.assertTrue(hrefs, '非アクティブ集会のリンクが見つからない')

        response = self.client.get(hrefs[0], HTTP_SEC_FETCH_SITE='same-origin')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session['active_community_id'], self.community2.id)

    def test_account_settings_link_exists(self):
        """アカウント設定リンクが存在する"""
        self.client.force_login(self.user)
        response = self.client.get(reverse('ta_hub:index'))

        self.assertEqual(response.status_code, 200)
        settings_url = reverse('account:settings')
        self.assertIn(f'href="{settings_url}"', self._account_menu_html(response))

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
