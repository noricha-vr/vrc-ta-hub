"""スタッフ権限でVket各操作にアクセスできることのテスト（参照: Issue #141）"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from community.models import Community, CommunityMember
from event.models import Event
from vket.models import VketCollaboration, VketParticipation, VketPresentation

User = get_user_model()


class StaffAccessTestBase(TestCase):
    """スタッフアクセステスト共通セットアップ"""

    def setUp(self):
        self.client = Client()
        self.staff_user = User.objects.create_user(
            user_name='staff_user',
            email='staff@example.com',
            password='testpass123',
        )
        self.non_member_user = User.objects.create_user(
            user_name='non_member',
            email='non_member@example.com',
            password='testpass123',
        )

        self.community = Community.objects.create(
            name='テスト集会',
            status='approved',
            frequency='毎週',
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.staff_user,
            role=CommunityMember.Role.STAFF,
        )

        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            slug='staff-access-test',
            name='Staff Access Test',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            phase=VketCollaboration.Phase.ENTRY_OPEN,
        )

        Event.objects.create(
            community=self.community,
            date=today,
            start_time='22:00',
            duration=60,
        )

    def _set_active_community(self):
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()


class StaffApplyViewTests(StaffAccessTestBase):
    """スタッフがApplyViewにアクセスできることのテスト"""

    def test_staff_can_access_apply_get(self):
        """staffロールのユーザーがApplyViewのGETにアクセスできる"""
        self.client.login(username='staff_user', password='testpass123')
        self._set_active_community()
        response = self.client.get(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)

    def test_staff_can_submit_apply_post(self):
        """staffロールのユーザーがApplyViewのPOSTで参加登録できる"""
        self.client.login(username='staff_user', password='testpass123')
        self._set_active_community()

        target_date = self.collaboration.period_start
        response = self.client.post(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk}),
            data={
                'requested_date': target_date.isoformat(),
                'requested_start_time': '21:00',
                'requested_duration': '60',
                'organizer_note': 'スタッフからの申請',
                'lt-TOTAL_FORMS': '1',
                'lt-INITIAL_FORMS': '0',
                'lt-MIN_NUM_FORMS': '0',
                'lt-MAX_NUM_FORMS': '20',
                'lt-0-speaker': 'テスト発表者',
                'lt-0-title': 'テスト発表',
                'lt-0-duration': '5',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            VketParticipation.objects.filter(
                collaboration=self.collaboration,
                community=self.community,
            ).exists()
        )

    def test_non_member_cannot_access_apply(self):
        """メンバーでないユーザーはApplyViewに403が返る"""
        self.client.login(username='non_member', password='testpass123')
        # non_member にはアクティブなコミュニティがないため membership=None で弾かれる
        response = self.client.get(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 403)


class StaffStageRegisterViewTests(StaffAccessTestBase):
    """スタッフがStageRegisterViewにアクセスできることのテスト"""

    def test_staff_can_register_stage(self):
        """staffロールのユーザーがステージ登録を完了できる"""
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
            progress=VketParticipation.Progress.APPLIED,
        )

        self.client.login(username='staff_user', password='testpass123')
        self._set_active_community()
        response = self.client.post(
            reverse('vket:stage_register', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 302)

        participation.refresh_from_db()
        self.assertEqual(
            participation.progress,
            VketParticipation.Progress.STAGE_REGISTERED,
        )

    def test_non_member_cannot_register_stage(self):
        """メンバーでないユーザーはステージ登録に403が返る"""
        VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
            progress=VketParticipation.Progress.APPLIED,
        )

        self.client.login(username='non_member', password='testpass123')
        response = self.client.post(
            reverse('vket:stage_register', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 403)


class StaffPresentationDeleteViewTests(StaffAccessTestBase):
    """スタッフがPresentationDeleteViewにアクセスできることのテスト"""

    def test_staff_can_delete_presentation(self):
        """staffロールのユーザーがLTを削除できる"""
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
        )
        presentation = VketPresentation.objects.create(
            participation=participation,
            speaker='テスト発表者',
            theme='テスト発表',
            duration=5,
        )

        self.client.login(username='staff_user', password='testpass123')
        self._set_active_community()
        response = self.client.post(
            reverse(
                'vket:presentation_delete',
                kwargs={
                    'pk': self.collaboration.pk,
                    'presentation_id': presentation.pk,
                },
            )
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(VketPresentation.objects.filter(pk=presentation.pk).exists())

    def test_non_member_cannot_delete_presentation(self):
        """メンバーでないユーザーはLT削除に403が返る"""
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
        )
        presentation = VketPresentation.objects.create(
            participation=participation,
            speaker='テスト発表者',
            theme='テスト発表',
            duration=5,
        )

        self.client.login(username='non_member', password='testpass123')
        response = self.client.post(
            reverse(
                'vket:presentation_delete',
                kwargs={
                    'pk': self.collaboration.pk,
                    'presentation_id': presentation.pk,
                },
            )
        )
        self.assertEqual(response.status_code, 403)
        # 削除されていないことを確認
        self.assertTrue(VketPresentation.objects.filter(pk=presentation.pk).exists())


class StaffCollaborationDetailViewTests(StaffAccessTestBase):
    """スタッフがCollaborationDetailViewでcan_apply=Trueになることのテスト"""

    def test_staff_sees_apply_button(self):
        """staffロールのユーザーにも参加申請ボタンが表示される"""
        self.client.login(username='staff_user', password='testpass123')
        self._set_active_community()
        response = self.client.get(
            reverse('vket:detail', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_apply'])
