"""主催者引き継ぎ機能のテスト"""
from datetime import timedelta

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone

from community.models import (
    Community,
    CommunityMember,
    CommunityInvitation,
    INVITATION_EXPIRATION_DAYS,
)

CustomUser = get_user_model()


class CreateOwnershipTransferViewTest(TestCase):
    """CreateOwnershipTransferViewのテスト"""

    def setUp(self):
        self.client = Client()

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

        self.community = Community.objects.create(
            name='テスト集会',
            status='approved',
            frequency='毎週'
        )

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

    def test_owner_can_create_transfer_link(self):
        """主催者は引き継ぎリンクを生成できる"""
        self.client.login(username='オーナー', password='testpass123')

        response = self.client.post(
            reverse('community:create_ownership_transfer', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            CommunityInvitation.objects.filter(
                community=self.community,
                created_by=self.owner,
                invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER
            ).exists()
        )

    def test_staff_cannot_create_transfer_link(self):
        """スタッフは引き継ぎリンクを生成できない"""
        self.client.login(username='スタッフ', password='testpass123')

        response = self.client.post(
            reverse('community:create_ownership_transfer', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            CommunityInvitation.objects.filter(
                community=self.community,
                invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER
            ).exists()
        )

    def test_non_member_cannot_create_transfer_link(self):
        """非メンバーは引き継ぎリンクを生成できない"""
        self.client.login(username='非メンバー', password='testpass123')

        response = self.client.post(
            reverse('community:create_ownership_transfer', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            CommunityInvitation.objects.filter(
                community=self.community,
                invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER
            ).exists()
        )

    def test_cannot_create_multiple_transfer_links(self):
        """有効な引き継ぎリンクは1つしか作成できない"""
        self.client.login(username='オーナー', password='testpass123')

        # 1つ目を作成
        self.client.post(
            reverse('community:create_ownership_transfer', kwargs={'pk': self.community.pk})
        )

        # 2つ目を作成しようとする
        self.client.post(
            reverse('community:create_ownership_transfer', kwargs={'pk': self.community.pk})
        )

        # 1つしか存在しないことを確認
        count = CommunityInvitation.objects.filter(
            community=self.community,
            invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER
        ).count()
        self.assertEqual(count, 1)


class AcceptOwnershipTransferViewTest(TestCase):
    """AcceptOwnershipTransferViewのテスト"""

    def setUp(self):
        self.client = Client()

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
        self.new_user = CustomUser.objects.create_user(
            email='newuser@example.com',
            password='testpass123',
            user_name='新規ユーザー'
        )

        self.community = Community.objects.create(
            name='テスト集会',
            status='approved',
            frequency='毎週'
        )

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

        self.invitation = CommunityInvitation.objects.create(
            community=self.community,
            created_by=self.owner,
            invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER,
            expires_at=timezone.now() + timedelta(days=INVITATION_EXPIRATION_DAYS)
        )

    def test_anonymous_can_view_transfer_page(self):
        """未ログインユーザーも引き継ぎページを閲覧できる"""
        response = self.client.get(
            reverse('community:accept_ownership_transfer', kwargs={'token': self.invitation.token})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.community.name)
        self.assertContains(response, 'ログインして引き継ぐ')

    def test_logged_in_user_can_view_transfer_page(self):
        """ログイン済みユーザーは引き継ぎページを閲覧できる"""
        self.client.login(username='新規ユーザー', password='testpass123')

        response = self.client.get(
            reverse('community:accept_ownership_transfer', kwargs={'token': self.invitation.token})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.community.name)
        self.assertContains(response, '主催者を引き継ぐ')

    def test_new_user_can_accept_transfer(self):
        """新規ユーザーは引き継ぎを受けて主催者になれる"""
        self.client.login(username='新規ユーザー', password='testpass123')

        response = self.client.post(
            reverse('community:accept_ownership_transfer', kwargs={'token': self.invitation.token})
        )

        self.assertEqual(response.status_code, 302)

        # 新ユーザーが主催者になっている
        self.assertTrue(
            CommunityMember.objects.filter(
                community=self.community,
                user=self.new_user,
                role=CommunityMember.Role.OWNER
            ).exists()
        )

        # 旧主催者がスタッフになっている
        old_owner_member = CommunityMember.objects.get(
            community=self.community,
            user=self.owner
        )
        self.assertEqual(old_owner_member.role, CommunityMember.Role.STAFF)

        # 新ユーザーがオーナーになっていることを確認
        self.community.refresh_from_db()
        self.assertTrue(self.community.is_owner(self.new_user))

        # 招待リンクが削除されている
        self.assertFalse(
            CommunityInvitation.objects.filter(pk=self.invitation.pk).exists()
        )

    def test_existing_staff_can_accept_transfer(self):
        """既存スタッフは引き継ぎを受けて主催者に昇格できる"""
        self.client.login(username='スタッフ', password='testpass123')

        response = self.client.post(
            reverse('community:accept_ownership_transfer', kwargs={'token': self.invitation.token})
        )

        self.assertEqual(response.status_code, 302)

        # スタッフが主催者になっている
        staff_member = CommunityMember.objects.get(
            community=self.community,
            user=self.staff
        )
        self.assertEqual(staff_member.role, CommunityMember.Role.OWNER)

        # 旧主催者がスタッフになっている
        old_owner_member = CommunityMember.objects.get(
            community=self.community,
            user=self.owner
        )
        self.assertEqual(old_owner_member.role, CommunityMember.Role.STAFF)

    def test_current_owner_cannot_accept_transfer(self):
        """現在の主催者は自分自身に引き継ぎできない"""
        self.client.login(username='オーナー', password='testpass123')

        response = self.client.post(
            reverse('community:accept_ownership_transfer', kwargs={'token': self.invitation.token})
        )

        self.assertEqual(response.status_code, 302)

        # 招待リンクは削除されていない
        self.assertTrue(
            CommunityInvitation.objects.filter(pk=self.invitation.pk).exists()
        )

    def test_anonymous_cannot_accept_transfer(self):
        """未ログインユーザーは引き継ぎを受けられない"""
        response = self.client.post(
            reverse('community:accept_ownership_transfer', kwargs={'token': self.invitation.token})
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_expired_transfer_shows_error(self):
        """期限切れの引き継ぎリンクはエラーを表示"""
        self.invitation.expires_at = timezone.now() - timedelta(seconds=1)
        self.invitation.save()

        response = self.client.get(
            reverse('community:accept_ownership_transfer', kwargs={'token': self.invitation.token})
        )

        self.assertEqual(response.status_code, 302)  # リダイレクト

    def test_expired_transfer_post_shows_error(self):
        """期限切れの引き継ぎリンクにPOSTするとエラー"""
        self.client.login(username='新規ユーザー', password='testpass123')

        self.invitation.expires_at = timezone.now() - timedelta(seconds=1)
        self.invitation.save()

        response = self.client.post(
            reverse('community:accept_ownership_transfer', kwargs={'token': self.invitation.token})
        )

        self.assertEqual(response.status_code, 302)

        # 新ユーザーは主催者になっていない
        self.assertFalse(
            CommunityMember.objects.filter(
                community=self.community,
                user=self.new_user,
                role=CommunityMember.Role.OWNER
            ).exists()
        )


class RevokeOwnershipTransferViewTest(TestCase):
    """RevokeOwnershipTransferViewのテスト"""

    def setUp(self):
        self.client = Client()

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

        self.community = Community.objects.create(
            name='テスト集会',
            status='approved',
            frequency='毎週'
        )

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

        self.invitation = CommunityInvitation.objects.create(
            community=self.community,
            created_by=self.owner,
            invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER,
            expires_at=timezone.now() + timedelta(days=INVITATION_EXPIRATION_DAYS)
        )

    def test_owner_can_revoke_transfer_link(self):
        """主催者は引き継ぎリンクを削除できる"""
        self.client.login(username='オーナー', password='testpass123')

        response = self.client.post(
            reverse('community:revoke_ownership_transfer', kwargs={
                'pk': self.community.pk,
                'invitation_id': self.invitation.pk
            })
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            CommunityInvitation.objects.filter(pk=self.invitation.pk).exists()
        )

    def test_staff_cannot_revoke_transfer_link(self):
        """スタッフは引き継ぎリンクを削除できない"""
        self.client.login(username='スタッフ', password='testpass123')

        response = self.client.post(
            reverse('community:revoke_ownership_transfer', kwargs={
                'pk': self.community.pk,
                'invitation_id': self.invitation.pk
            })
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            CommunityInvitation.objects.filter(pk=self.invitation.pk).exists()
        )


class CommunitySettingsOwnershipTransferTest(TestCase):
    """集会設定ページでの引き継ぎリンク表示テスト"""

    def setUp(self):
        self.client = Client()

        self.owner = CustomUser.objects.create_user(
            email='owner@example.com',
            password='testpass123',
            user_name='オーナー'
        )

        self.community = Community.objects.create(
            name='テスト集会',
            status='approved',
            frequency='毎週'
        )

        CommunityMember.objects.create(
            community=self.community,
            user=self.owner,
            role=CommunityMember.Role.OWNER
        )

    def test_transfer_section_is_displayed_for_owner(self):
        """主催者には引き継ぎセクションが表示される"""
        self.client.login(username='オーナー', password='testpass123')
        session = self.client.session
        session['active_community_id'] = self.community.pk
        session.save()

        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '主催者の引き継ぎ')
        self.assertContains(response, '引き継ぎリンクを作成')

    def test_active_transfer_link_is_displayed(self):
        """有効な引き継ぎリンクが表示される"""
        invitation = CommunityInvitation.objects.create(
            community=self.community,
            created_by=self.owner,
            invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER,
            expires_at=timezone.now() + timedelta(days=INVITATION_EXPIRATION_DAYS)
        )

        self.client.login(username='オーナー', password='testpass123')
        session = self.client.session
        session['active_community_id'] = self.community.pk
        session.save()

        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '有効な引き継ぎリンクがあります')
        self.assertContains(response, invitation.token)

    def test_expired_transfer_link_is_not_displayed(self):
        """期限切れの引き継ぎリンクは表示されない"""
        invitation = CommunityInvitation.objects.create(
            community=self.community,
            created_by=self.owner,
            invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER,
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        self.client.login(username='オーナー', password='testpass123')
        session = self.client.session
        session['active_community_id'] = self.community.pk
        session.save()

        response = self.client.get(reverse('community:settings'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, invitation.token)
        self.assertContains(response, '引き継ぎリンクを作成')


class InvitationTypeModelTest(TestCase):
    """CommunityInvitation.InvitationTypeのテスト"""

    def setUp(self):
        self.owner = CustomUser.objects.create_user(
            email='owner@example.com',
            password='testpass123',
            user_name='オーナー'
        )
        self.community = Community.objects.create(
            name='テスト集会',
            status='approved',
            frequency='毎週'
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner,
            role=CommunityMember.Role.OWNER
        )

    def test_default_invitation_type_is_staff(self):
        """招待タイプのデフォルトはスタッフ招待"""
        invitation = CommunityInvitation.objects.create(
            community=self.community,
            created_by=self.owner
        )
        self.assertEqual(
            invitation.invitation_type,
            CommunityInvitation.InvitationType.STAFF
        )

    def test_can_create_ownership_transfer_invitation(self):
        """主催者引き継ぎタイプの招待を作成できる"""
        invitation = CommunityInvitation.objects.create(
            community=self.community,
            created_by=self.owner,
            invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER
        )
        self.assertEqual(
            invitation.invitation_type,
            CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER
        )

    def test_different_types_can_coexist(self):
        """スタッフ招待と引き継ぎリンクは別々に存在できる"""
        staff_invitation = CommunityInvitation.objects.create(
            community=self.community,
            created_by=self.owner,
            invitation_type=CommunityInvitation.InvitationType.STAFF
        )
        transfer_invitation = CommunityInvitation.objects.create(
            community=self.community,
            created_by=self.owner,
            invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER
        )

        self.assertEqual(
            CommunityInvitation.objects.filter(community=self.community).count(),
            2
        )
        self.assertNotEqual(staff_invitation.token, transfer_invitation.token)
