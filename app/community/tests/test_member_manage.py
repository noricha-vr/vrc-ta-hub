from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site

from allauth.socialaccount.models import SocialApp

from community.models import Community, CommunityMember

CustomUser = get_user_model()

# テスト用のSOCIALACCOUNT_PROVIDERS設定（APPSなし）
# これにより、データベースのSocialAppのみが使用される
TEST_SOCIALACCOUNT_PROVIDERS = {
    'discord': {
        'SCOPE': ['identify', 'email'],
    }
}


class CommunityMemberManageViewTest(TestCase):
    """CommunityMemberManageViewのテスト"""

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

    def test_owner_can_access_member_manage(self):
        """主催者はメンバー管理ページにアクセスできる"""
        self.client.login(username='オーナー', password='testpass123')

        response = self.client.get(
            reverse('community:member_manage', kwargs={'pk': self.community.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'メンバー管理')

    def test_staff_cannot_access_member_manage(self):
        """スタッフはメンバー管理ページにアクセスできない"""
        self.client.login(username='スタッフ', password='testpass123')

        response = self.client.get(
            reverse('community:member_manage', kwargs={'pk': self.community.pk})
        )
        self.assertEqual(response.status_code, 403)

    def test_non_member_cannot_access_member_manage(self):
        """非メンバーはメンバー管理ページにアクセスできない"""
        self.client.login(username='非メンバー', password='testpass123')

        response = self.client.get(
            reverse('community:member_manage', kwargs={'pk': self.community.pk})
        )
        self.assertEqual(response.status_code, 403)

    def test_anonymous_redirects_to_login(self):
        """未ログインユーザーはログインページにリダイレクトされる"""
        response = self.client.get(
            reverse('community:member_manage', kwargs={'pk': self.community.pk})
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)


class RemoveStaffViewTest(TestCase):
    """RemoveStaffViewのテスト"""

    def setUp(self):
        self.client = Client()

        # テスト用ユーザーを作成
        self.owner1 = CustomUser.objects.create_user(
            email='owner1@example.com',
            password='testpass123',
            user_name='オーナー1'
        )
        self.owner2 = CustomUser.objects.create_user(
            email='owner2@example.com',
            password='testpass123',
            user_name='オーナー2'
        )
        self.staff = CustomUser.objects.create_user(
            email='staff@example.com',
            password='testpass123',
            user_name='スタッフ'
        )

        # テスト用集会を作成
        self.community = Community.objects.create(
            name='テスト集会',
            status='approved',
            frequency='毎週'
        )

        # CommunityMemberを作成
        self.owner1_member = CommunityMember.objects.create(
            community=self.community,
            user=self.owner1,
            role=CommunityMember.Role.OWNER
        )
        self.owner2_member = CommunityMember.objects.create(
            community=self.community,
            user=self.owner2,
            role=CommunityMember.Role.OWNER
        )
        self.staff_member = CommunityMember.objects.create(
            community=self.community,
            user=self.staff,
            role=CommunityMember.Role.STAFF
        )

    def test_owner_can_remove_staff(self):
        """主催者はスタッフを削除できる"""
        self.client.login(username='オーナー1', password='testpass123')

        response = self.client.post(
            reverse('community:remove_staff', kwargs={
                'pk': self.community.pk,
                'member_id': self.staff_member.pk
            })
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            CommunityMember.objects.filter(pk=self.staff_member.pk).exists()
        )

    def test_owner_can_remove_other_owner(self):
        """主催者は他の主催者を削除できる"""
        self.client.login(username='オーナー1', password='testpass123')

        response = self.client.post(
            reverse('community:remove_staff', kwargs={
                'pk': self.community.pk,
                'member_id': self.owner2_member.pk
            })
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            CommunityMember.objects.filter(pk=self.owner2_member.pk).exists()
        )

    def test_owner_cannot_remove_self(self):
        """主催者は自分自身を削除できない"""
        self.client.login(username='オーナー1', password='testpass123')

        response = self.client.post(
            reverse('community:remove_staff', kwargs={
                'pk': self.community.pk,
                'member_id': self.owner1_member.pk
            })
        )

        self.assertEqual(response.status_code, 302)
        # 削除されていないことを確認
        self.assertTrue(
            CommunityMember.objects.filter(pk=self.owner1_member.pk).exists()
        )

    def test_cannot_remove_last_owner(self):
        """最後の主催者は削除できない"""
        # owner2を削除
        self.owner2_member.delete()

        self.client.login(username='オーナー1', password='testpass123')

        # スタッフとして別のユーザーがowner1を削除しようとしてもエラー
        # 実際にはowner1はこの時点で唯一の主催者なので、自分を削除できない
        # 別の主催者を追加してから、その主催者として削除を試みる
        new_owner = CustomUser.objects.create_user(
            email='newowner@example.com',
            password='testpass123',
            user_name='新オーナー'
        )
        new_owner_member = CommunityMember.objects.create(
            community=self.community,
            user=new_owner,
            role=CommunityMember.Role.OWNER
        )

        # 新オーナーでログイン
        self.client.login(username='新オーナー', password='testpass123')

        # owner1を削除しようとする（この時点で2人の主催者がいるので可能）
        response = self.client.post(
            reverse('community:remove_staff', kwargs={
                'pk': self.community.pk,
                'member_id': self.owner1_member.pk
            })
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            CommunityMember.objects.filter(pk=self.owner1_member.pk).exists()
        )

        # 今度は新オーナーが唯一の主催者になっている
        # 新オーナーは自分自身を削除できない
        response = self.client.post(
            reverse('community:remove_staff', kwargs={
                'pk': self.community.pk,
                'member_id': new_owner_member.pk
            })
        )

        self.assertEqual(response.status_code, 302)
        # 削除されていないことを確認（唯一の主催者なので）
        self.assertTrue(
            CommunityMember.objects.filter(pk=new_owner_member.pk).exists()
        )

    def test_staff_cannot_remove_members(self):
        """スタッフはメンバーを削除できない"""
        self.client.login(username='スタッフ', password='testpass123')

        response = self.client.post(
            reverse('community:remove_staff', kwargs={
                'pk': self.community.pk,
                'member_id': self.owner2_member.pk
            })
        )

        self.assertEqual(response.status_code, 302)
        # 削除されていないことを確認
        self.assertTrue(
            CommunityMember.objects.filter(pk=self.owner2_member.pk).exists()
        )


@override_settings(SOCIALACCOUNT_PROVIDERS=TEST_SOCIALACCOUNT_PROVIDERS)
class SettingsPageCommunityDropdownTest(TestCase):
    """設定ページの集会ドロップダウンテスト"""

    def setUp(self):
        # Discord SocialAppを作成（settings.htmlで{% provider_login_url 'discord' %}が使われているため）
        # override_settingsでAPPS設定を無効化しているため、DBのSocialAppが使用される
        site = Site.objects.get_current()
        social_app = SocialApp.objects.create(
            provider='discord',
            name='Discord Test',
            client_id='test-client-id',
            secret='test-secret'
        )
        social_app.sites.add(site)

        self.client = Client()

        # テスト用ユーザーを作成
        self.user = CustomUser.objects.create_user(
            email='user@example.com',
            password='testpass123',
            user_name='テストユーザー'
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

    def test_no_dropdown_when_no_communities(self):
        """集会がない場合はドロップダウンが表示されない"""
        self.client.login(username='テストユーザー', password='testpass123')

        response = self.client.get(reverse('account:settings'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '管理中の集会を切り替え')

    def test_no_dropdown_when_one_community(self):
        """集会が1つの場合はドロップダウンが表示されない"""
        CommunityMember.objects.create(
            community=self.community1,
            user=self.user,
            role=CommunityMember.Role.OWNER
        )
        self.client.login(username='テストユーザー', password='testpass123')

        response = self.client.get(reverse('account:settings'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '管理中の集会を切り替え')
        self.assertContains(response, '管理中の集会')
        self.assertContains(response, '集会1')

    def test_dropdown_when_multiple_communities(self):
        """集会が複数の場合はドロップダウンが表示される"""
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
        self.client.login(username='テストユーザー', password='testpass123')

        response = self.client.get(reverse('account:settings'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '管理中の集会を切り替え')
        self.assertContains(response, '集会1')
        self.assertContains(response, '集会2')


@override_settings(SOCIALACCOUNT_PROVIDERS=TEST_SOCIALACCOUNT_PROVIDERS)
class MemberManageLinkTest(TestCase):
    """メンバー管理リンクのテスト"""

    def setUp(self):
        # Discord SocialAppを作成（settings.htmlで{% provider_login_url 'discord' %}が使われているため）
        # override_settingsでAPPS設定を無効化しているため、DBのSocialAppが使用される
        site = Site.objects.get_current()
        social_app = SocialApp.objects.create(
            provider='discord',
            name='Discord Test',
            client_id='test-client-id',
            secret='test-secret'
        )
        social_app.sites.add(site)

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

    def test_owner_sees_member_manage_link_on_settings(self):
        """主催者は設定ページでメンバー管理リンクを見ることができる"""
        self.client.login(username='オーナー', password='testpass123')

        response = self.client.get(reverse('account:settings'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'メンバー管理')

    def test_staff_does_not_see_member_manage_link_on_settings(self):
        """スタッフは設定ページでメンバー管理リンクを見ることができない"""
        self.client.login(username='スタッフ', password='testpass123')

        response = self.client.get(reverse('account:settings'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'メンバー管理')

    def test_owner_sees_member_manage_link_on_update(self):
        """主催者は集会編集ページでメンバー管理リンクを見ることができる"""
        self.client.login(username='オーナー', password='testpass123')
        # セッションにactive_community_idを設定
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

        response = self.client.get(reverse('community:update'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'メンバー管理')

    def test_staff_does_not_see_member_manage_link_on_update(self):
        """スタッフは集会編集ページでメンバー管理リンクを見ることができない"""
        self.client.login(username='スタッフ', password='testpass123')
        # セッションにactive_community_idを設定
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

        response = self.client.get(reverse('community:update'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'メンバー管理')
