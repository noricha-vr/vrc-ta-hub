from django.test import TestCase
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from community.models import Community, CommunityMember

CustomUser = get_user_model()


class CommunityMemberModelTest(TestCase):
    """CommunityMemberモデルのテスト"""

    def setUp(self):
        # テスト用ユーザーを作成
        self.owner_user = CustomUser.objects.create_user(
            email='owner@example.com',
            password='testpass123',
            user_name='オーナーユーザー'
        )
        self.staff_user = CustomUser.objects.create_user(
            email='staff@example.com',
            password='testpass123',
            user_name='スタッフユーザー'
        )
        self.other_user = CustomUser.objects.create_user(
            email='other@example.com',
            password='testpass123',
            user_name='その他ユーザー'
        )

        # テスト用集会を作成
        self.community = Community.objects.create(
            name='テスト集会',
            status='approved',
            frequency='毎週'
        )

        # CommunityMemberを作成
        self.owner_member = CommunityMember.objects.create(
            community=self.community,
            user=self.owner_user,
            role=CommunityMember.Role.OWNER
        )
        self.staff_member = CommunityMember.objects.create(
            community=self.community,
            user=self.staff_user,
            role=CommunityMember.Role.STAFF
        )

    def test_community_member_str(self):
        """CommunityMemberの__str__メソッドをテスト"""
        expected = f'{self.owner_user.user_name} - {self.community.name} (主催者)'
        self.assertEqual(str(self.owner_member), expected)

        expected_staff = f'{self.staff_user.user_name} - {self.community.name} (スタッフ)'
        self.assertEqual(str(self.staff_member), expected_staff)

    def test_is_owner_property(self):
        """is_ownerプロパティをテスト"""
        self.assertTrue(self.owner_member.is_owner)
        self.assertFalse(self.staff_member.is_owner)

    def test_can_delete_property(self):
        """can_deleteプロパティをテスト"""
        self.assertTrue(self.owner_member.can_delete)
        self.assertFalse(self.staff_member.can_delete)

    def test_can_edit_property(self):
        """can_editプロパティをテスト"""
        self.assertTrue(self.owner_member.can_edit)
        self.assertTrue(self.staff_member.can_edit)

    def test_unique_together_constraint(self):
        """同じcommunityとuserの組み合わせは作成できないことをテスト"""
        with self.assertRaises(IntegrityError):
            CommunityMember.objects.create(
                community=self.community,
                user=self.owner_user,
                role=CommunityMember.Role.STAFF
            )


class CommunityHelperMethodsTest(TestCase):
    """Communityのヘルパーメソッドのテスト"""

    def setUp(self):
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
        self.staff1 = CustomUser.objects.create_user(
            email='staff1@example.com',
            password='testpass123',
            user_name='スタッフ1'
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
            user=self.owner1,
            role=CommunityMember.Role.OWNER
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner2,
            role=CommunityMember.Role.OWNER
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.staff1,
            role=CommunityMember.Role.STAFF
        )

    def test_get_owners(self):
        """get_ownersメソッドをテスト"""
        owners = self.community.get_owners()
        self.assertEqual(len(owners), 2)
        self.assertIn(self.owner1, owners)
        self.assertIn(self.owner2, owners)
        self.assertNotIn(self.staff1, owners)

    def test_get_staff(self):
        """get_staffメソッドをテスト"""
        staff = self.community.get_staff()
        self.assertEqual(len(staff), 1)
        self.assertIn(self.staff1, staff)
        self.assertNotIn(self.owner1, staff)

    def test_get_all_managers(self):
        """get_all_managersメソッドをテスト"""
        managers = self.community.get_all_managers()
        self.assertEqual(len(managers), 3)
        self.assertIn(self.owner1, managers)
        self.assertIn(self.owner2, managers)
        self.assertIn(self.staff1, managers)
        self.assertNotIn(self.non_member, managers)

    def test_is_manager(self):
        """is_managerメソッドをテスト"""
        self.assertTrue(self.community.is_manager(self.owner1))
        self.assertTrue(self.community.is_manager(self.staff1))
        self.assertFalse(self.community.is_manager(self.non_member))

    def test_is_owner(self):
        """is_ownerメソッドをテスト"""
        self.assertTrue(self.community.is_owner(self.owner1))
        self.assertTrue(self.community.is_owner(self.owner2))
        self.assertFalse(self.community.is_owner(self.staff1))
        self.assertFalse(self.community.is_owner(self.non_member))

    def test_can_edit(self):
        """can_editメソッドをテスト"""
        self.assertTrue(self.community.can_edit(self.owner1))
        self.assertTrue(self.community.can_edit(self.staff1))
        self.assertFalse(self.community.can_edit(self.non_member))

    def test_can_delete(self):
        """can_deleteメソッドをテスト"""
        self.assertTrue(self.community.can_delete(self.owner1))
        self.assertTrue(self.community.can_delete(self.owner2))
        self.assertFalse(self.community.can_delete(self.staff1))
        self.assertFalse(self.community.can_delete(self.non_member))


