from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from community.models import Community, CommunityMember

CustomUser = get_user_model()


class SwitchCommunityViewTest(TestCase):
    """SwitchCommunityViewのテスト"""

    def setUp(self):
        self.client = Client()

        # テスト用ユーザーを作成
        self.user = CustomUser.objects.create_user(
            email='user@example.com',
            password='testpass123',
            user_name='テストユーザー'
        )
        self.other_user = CustomUser.objects.create_user(
            email='other@example.com',
            password='testpass123',
            user_name='他のユーザー'
        )

        # テスト用集会を作成
        self.community1 = Community.objects.create(
            name='集会1',
            status='approved',
            frequency='毎週'
        )
        self.community2 = Community.objects.create(
            name='集会2',
            status='approved',
            frequency='隔週'
        )
        self.other_community = Community.objects.create(
            name='他の集会',
            status='approved',
            frequency='毎月'
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
        CommunityMember.objects.create(
            community=self.other_community,
            user=self.other_user,
            role=CommunityMember.Role.OWNER
        )

    def test_switch_community_requires_login(self):
        """ログインが必要"""
        response = self.client.post(
            reverse('community:switch'),
            {'community_id': self.community1.id}
        )
        # ログインページにリダイレクト
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_switch_to_valid_community(self):
        """有効な集会に切り替えできる"""
        self.client.login(username='テストユーザー', password='testpass123')

        response = self.client.post(
            reverse('community:switch'),
            {'community_id': self.community2.id},
            HTTP_REFERER='/account/settings/'
        )

        # リダイレクトを確認
        self.assertEqual(response.status_code, 302)

        # セッションにactive_community_idが設定されていることを確認
        session = self.client.session
        self.assertEqual(session['active_community_id'], self.community2.id)

    def test_cannot_switch_to_other_users_community(self):
        """他のユーザーの集会には切り替えできない"""
        self.client.login(username='テストユーザー', password='testpass123')

        response = self.client.post(
            reverse('community:switch'),
            {'community_id': self.other_community.id},
            HTTP_REFERER='/account/settings/'
        )

        # リダイレクトを確認
        self.assertEqual(response.status_code, 302)

        # セッションにactive_community_idが設定されていないことを確認
        session = self.client.session
        self.assertNotEqual(session.get('active_community_id'), self.other_community.id)

    def test_switch_without_community_id(self):
        """community_idがない場合はエラー"""
        self.client.login(username='テストユーザー', password='testpass123')

        response = self.client.post(
            reverse('community:switch'),
            {},
            HTTP_REFERER='/account/settings/'
        )

        # リダイレクトを確認
        self.assertEqual(response.status_code, 302)

    def test_redirects_to_referer(self):
        """元のページにリダイレクトする"""
        self.client.login(username='テストユーザー', password='testpass123')

        response = self.client.post(
            reverse('community:switch'),
            {'community_id': self.community1.id},
            HTTP_REFERER='/event/my_list/'
        )

        self.assertRedirects(response, '/event/my_list/', fetch_redirect_response=False)

    def test_switch_with_invalid_community_id(self):
        """無効なcommunity_id（数値でない文字列）の場合はエラーメッセージが表示される"""
        self.client.login(username='テストユーザー', password='testpass123')

        response = self.client.post(
            reverse('community:switch'),
            {'community_id': 'invalid_string'},
            HTTP_REFERER='/account/settings/'
        )

        # リダイレクトを確認
        self.assertEqual(response.status_code, 302)

        # セッションにactive_community_idが設定されていないことを確認
        session = self.client.session
        self.assertIsNone(session.get('active_community_id'))

    def test_switch_with_non_integer_community_id(self):
        """小数点を含むcommunity_idの場合はエラーメッセージが表示される"""
        self.client.login(username='テストユーザー', password='testpass123')

        response = self.client.post(
            reverse('community:switch'),
            {'community_id': '1.5'},
            HTTP_REFERER='/account/settings/'
        )

        # リダイレクトを確認
        self.assertEqual(response.status_code, 302)

        # セッションにactive_community_idが設定されていないことを確認
        session = self.client.session
        self.assertIsNone(session.get('active_community_id'))


class CommunityUpdateViewPermissionTest(TestCase):
    """CommunityUpdateViewの権限テスト"""

    def setUp(self):
        self.client = Client()

        # テスト用ユーザーを作成
        self.owner = CustomUser.objects.create_user(
            email='owner@example.com',
            password='testpass123',
            user_name='オーナー'
        )
        self.staff = CustomUser.objects.create_user(
            email='staff@example.com',
            password='testpass123',
            user_name='スタッフ'
        )
        self.non_member = CustomUser.objects.create_user(
            email='nonmember@example.com',
            password='testpass123',
            user_name='非メンバー'
        )

        # テスト用集会を作成
        self.community = Community.objects.create(
            name='テスト集会',
            status='approved',
            frequency='毎週'
        )

        # CommunityMemberを作成
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner,
            role=CommunityMember.Role.OWNER
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.staff,
            role=CommunityMember.Role.STAFF
        )

    def test_owner_can_access_update_view(self):
        """主催者は更新ビューにアクセスできる"""
        self.client.login(username='オーナー', password='testpass123')
        # セッションにactive_community_idを設定
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

        response = self.client.get(reverse('community:update'))
        self.assertEqual(response.status_code, 200)

    def test_staff_can_access_update_view(self):
        """スタッフは更新ビューにアクセスできる"""
        self.client.login(username='スタッフ', password='testpass123')
        # セッションにactive_community_idを設定
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

        response = self.client.get(reverse('community:update'))
        self.assertEqual(response.status_code, 200)

    def test_non_member_cannot_access_update_view(self):
        """非メンバーは更新ビューにアクセスできない"""
        self.client.login(username='非メンバー', password='testpass123')

        response = self.client.get(reverse('community:update'))
        # 403 Forbiddenになる
        self.assertEqual(response.status_code, 403)


class CloseCommunityViewPermissionTest(TestCase):
    """CloseCommunityViewの権限テスト"""

    def setUp(self):
        self.client = Client()

        # テスト用ユーザーを作成
        self.owner = CustomUser.objects.create_user(
            email='owner@example.com',
            password='testpass123',
            user_name='オーナー'
        )
        self.staff = CustomUser.objects.create_user(
            email='staff@example.com',
            password='testpass123',
            user_name='スタッフ'
        )
        self.admin = CustomUser.objects.create_superuser(
            email='admin@example.com',
            password='testpass123',
            user_name='管理者'
        )

        # テスト用集会を作成
        self.community = Community.objects.create(
            name='テスト集会',
            status='approved',
            frequency='毎週'
        )

        # CommunityMemberを作成
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner,
            role=CommunityMember.Role.OWNER
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.staff,
            role=CommunityMember.Role.STAFF
        )

    def test_owner_can_close_community(self):
        """主催者は集会を閉鎖できる"""
        self.client.login(username='オーナー', password='testpass123')

        response = self.client.post(reverse('community:close', kwargs={'pk': self.community.pk}))

        # リダイレクトを確認
        self.assertEqual(response.status_code, 302)

        # 集会が閉鎖されていることを確認
        self.community.refresh_from_db()
        self.assertIsNotNone(self.community.end_at)

    def test_staff_cannot_close_community(self):
        """スタッフは集会を閉鎖できない"""
        self.client.login(username='スタッフ', password='testpass123')

        response = self.client.post(reverse('community:close', kwargs={'pk': self.community.pk}))

        # 403 Forbiddenになる
        self.assertEqual(response.status_code, 403)

    def test_admin_can_close_community(self):
        """管理者は集会を閉鎖できる"""
        self.client.login(username='管理者', password='testpass123')

        response = self.client.post(reverse('community:close', kwargs={'pk': self.community.pk}))

        # リダイレクトを確認
        self.assertEqual(response.status_code, 302)

        # 集会が閉鎖されていることを確認
        self.community.refresh_from_db()
        self.assertIsNotNone(self.community.end_at)
