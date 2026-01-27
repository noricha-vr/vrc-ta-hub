from datetime import timedelta

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone

from community.models import Community, CommunityMember, CommunityInvitation, INVITATION_EXPIRATION_DAYS

CustomUser = get_user_model()


class CommunityInvitationModelTest(TestCase):
    """CommunityInvitationモデルのテスト"""

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

    def test_token_generation(self):
        """トークンが自動生成される"""
        invitation = CommunityInvitation.objects.create(
            community=self.community,
            created_by=self.owner
        )
        self.assertIsNotNone(invitation.token)
        self.assertGreater(len(invitation.token), 32)  # secrets.token_urlsafe(48)は64文字程度

    def test_token_uniqueness(self):
        """トークンはユニーク"""
        invitation1 = CommunityInvitation.objects.create(
            community=self.community,
            created_by=self.owner
        )
        invitation2 = CommunityInvitation.objects.create(
            community=self.community,
            created_by=self.owner
        )
        self.assertNotEqual(invitation1.token, invitation2.token)

    def test_expires_at_default(self):
        """有効期限のデフォルト値が設定される"""
        before = timezone.now()
        invitation = CommunityInvitation.objects.create(
            community=self.community,
            created_by=self.owner
        )
        after = timezone.now()

        expected_min = before + timedelta(days=INVITATION_EXPIRATION_DAYS)
        expected_max = after + timedelta(days=INVITATION_EXPIRATION_DAYS)

        self.assertGreaterEqual(invitation.expires_at, expected_min)
        self.assertLessEqual(invitation.expires_at, expected_max)

    def test_is_valid_returns_true_for_active_invitation(self):
        """有効な招待はis_valid=True"""
        invitation = CommunityInvitation.objects.create(
            community=self.community,
            created_by=self.owner,
            expires_at=timezone.now() + timedelta(days=1)
        )
        self.assertTrue(invitation.is_valid)

    def test_is_valid_returns_false_for_expired_invitation(self):
        """期限切れの招待はis_valid=False"""
        invitation = CommunityInvitation.objects.create(
            community=self.community,
            created_by=self.owner,
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertFalse(invitation.is_valid)

    def test_create_invitation_class_method(self):
        """create_invitationクラスメソッドで招待を作成できる"""
        invitation = CommunityInvitation.create_invitation(self.community, self.owner)
        self.assertEqual(invitation.community, self.community)
        self.assertEqual(invitation.created_by, self.owner)
        self.assertIsNotNone(invitation.token)
        self.assertTrue(invitation.is_valid)


class CreateInvitationViewTest(TestCase):
    """CreateInvitationViewのテスト"""

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

    def test_owner_can_create_invitation(self):
        """主催者は招待リンクを生成できる"""
        self.client.login(username='オーナー', password='testpass123')

        response = self.client.post(
            reverse('community:create_invitation', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            CommunityInvitation.objects.filter(
                community=self.community,
                created_by=self.owner
            ).exists()
        )

    def test_staff_cannot_create_invitation(self):
        """スタッフは招待リンクを生成できない"""
        self.client.login(username='スタッフ', password='testpass123')

        response = self.client.post(
            reverse('community:create_invitation', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            CommunityInvitation.objects.filter(community=self.community).exists()
        )

    def test_non_member_cannot_create_invitation(self):
        """非メンバーは招待リンクを生成できない"""
        self.client.login(username='非メンバー', password='testpass123')

        response = self.client.post(
            reverse('community:create_invitation', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            CommunityInvitation.objects.filter(community=self.community).exists()
        )


class AcceptInvitationViewTest(TestCase):
    """AcceptInvitationViewのテスト"""

    def setUp(self):
        self.client = Client()

        self.owner = CustomUser.objects.create_user(
            email='owner@example.com',
            password='testpass123',
            user_name='オーナー'
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

        self.invitation = CommunityInvitation.create_invitation(
            self.community, self.owner
        )

    def test_anonymous_can_view_invitation_page(self):
        """未ログインユーザーも招待ページを閲覧できる"""
        response = self.client.get(
            reverse('community:accept_invitation', kwargs={'token': self.invitation.token})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.community.name)
        self.assertContains(response, 'ログインして参加')

    def test_logged_in_user_can_view_invitation_page(self):
        """ログイン済みユーザーは招待ページを閲覧できる"""
        self.client.login(username='新規ユーザー', password='testpass123')

        response = self.client.get(
            reverse('community:accept_invitation', kwargs={'token': self.invitation.token})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.community.name)
        self.assertContains(response, '招待を受ける')

    def test_user_can_accept_invitation(self):
        """ユーザーは招待を受けてスタッフになれる"""
        self.client.login(username='新規ユーザー', password='testpass123')

        response = self.client.post(
            reverse('community:accept_invitation', kwargs={'token': self.invitation.token})
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            CommunityMember.objects.filter(
                community=self.community,
                user=self.new_user,
                role=CommunityMember.Role.STAFF
            ).exists()
        )

    def test_anonymous_cannot_accept_invitation(self):
        """未ログインユーザーは招待を受けられない"""
        response = self.client.post(
            reverse('community:accept_invitation', kwargs={'token': self.invitation.token})
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)
        self.assertFalse(
            CommunityMember.objects.filter(
                community=self.community,
                user=self.new_user
            ).exists()
        )

    def test_expired_invitation_shows_error(self):
        """期限切れの招待はエラーを表示"""
        self.invitation.expires_at = timezone.now() - timedelta(seconds=1)
        self.invitation.save()

        response = self.client.get(
            reverse('community:accept_invitation', kwargs={'token': self.invitation.token})
        )

        self.assertEqual(response.status_code, 302)  # リダイレクト

    def test_expired_invitation_post_shows_error(self):
        """期限切れの招待にPOSTするとエラー"""
        self.client.login(username='新規ユーザー', password='testpass123')

        self.invitation.expires_at = timezone.now() - timedelta(seconds=1)
        self.invitation.save()

        response = self.client.post(
            reverse('community:accept_invitation', kwargs={'token': self.invitation.token})
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            CommunityMember.objects.filter(
                community=self.community,
                user=self.new_user
            ).exists()
        )

    def test_existing_member_sees_info_message(self):
        """既存メンバーには情報メッセージを表示"""
        self.client.login(username='オーナー', password='testpass123')

        response = self.client.get(
            reverse('community:accept_invitation', kwargs={'token': self.invitation.token})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '既にこの集会のメンバーです')

    def test_existing_member_cannot_be_added_twice(self):
        """既存メンバーは重複して追加されない"""
        self.client.login(username='オーナー', password='testpass123')
        initial_count = CommunityMember.objects.filter(
            community=self.community, user=self.owner
        ).count()

        response = self.client.post(
            reverse('community:accept_invitation', kwargs={'token': self.invitation.token})
        )

        self.assertEqual(response.status_code, 302)
        final_count = CommunityMember.objects.filter(
            community=self.community, user=self.owner
        ).count()
        self.assertEqual(initial_count, final_count)


class RevokeInvitationViewTest(TestCase):
    """RevokeInvitationViewのテスト"""

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

        self.invitation = CommunityInvitation.create_invitation(
            self.community, self.owner
        )

    def test_owner_can_revoke_invitation(self):
        """主催者は招待リンクを削除できる"""
        self.client.login(username='オーナー', password='testpass123')

        response = self.client.post(
            reverse('community:revoke_invitation', kwargs={
                'pk': self.community.pk,
                'invitation_id': self.invitation.pk
            })
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            CommunityInvitation.objects.filter(pk=self.invitation.pk).exists()
        )

    def test_staff_cannot_revoke_invitation(self):
        """スタッフは招待リンクを削除できない"""
        self.client.login(username='スタッフ', password='testpass123')

        response = self.client.post(
            reverse('community:revoke_invitation', kwargs={
                'pk': self.community.pk,
                'invitation_id': self.invitation.pk
            })
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            CommunityInvitation.objects.filter(pk=self.invitation.pk).exists()
        )


class MemberManageViewInvitationTest(TestCase):
    """メンバー管理ページでの招待リンク表示テスト"""

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

    def test_invitation_section_is_displayed(self):
        """招待リンクセクションが表示される"""
        self.client.login(username='オーナー', password='testpass123')

        response = self.client.get(
            reverse('community:member_manage', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '招待リンク')
        self.assertContains(response, '招待リンクを生成')

    def test_active_invitations_are_displayed(self):
        """有効な招待リンクが表示される"""
        invitation = CommunityInvitation.create_invitation(
            self.community, self.owner
        )

        self.client.login(username='オーナー', password='testpass123')

        response = self.client.get(
            reverse('community:member_manage', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, invitation.token[:10])

    def test_expired_invitations_are_not_displayed(self):
        """期限切れの招待リンクは表示されない"""
        invitation = CommunityInvitation.objects.create(
            community=self.community,
            created_by=self.owner,
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        self.client.login(username='オーナー', password='testpass123')

        response = self.client.get(
            reverse('community:member_manage', kwargs={'pk': self.community.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, invitation.token[:10])
