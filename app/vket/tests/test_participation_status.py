"""Vketコラボ機能のテスト."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from community.models import Community, CommunityMember
from vket.models import (
    VketCollaboration,
    VketNotice,
    VketNoticeReceipt,
    VketParticipation,
)


User = get_user_model()


class VketParticipationStatusTests(TestCase):
    """ParticipationStatusView のテスト（未確認お知らせバナー等）"""

    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(
            user_name='status_owner',
            email='status_owner@example.com',
            password='testpass123',
        )
        self.community = Community.objects.create(
            name='ステータステスト集会',
            status='approved',
            frequency='毎週',
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner,
            role=CommunityMember.Role.OWNER,
        )

        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            slug='vket-2026-status-test',
            name='ステータステスト',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            phase=VketCollaboration.Phase.SCHEDULING,
        )
        self.participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
        )

    def _set_active_community(self):
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

    def test_status_page_shows_unacked_count(self):
        """未確認のrequires_ackお知らせがあるとバナーに件数が表示される"""
        superuser = User.objects.create_superuser(
            user_name='su_status', email='su_status@example.com', password='p',
        )
        notice = VketNotice.objects.create(
            collaboration=self.collaboration,
            title='要確認',
            body='本文',
            requires_ack=True,
            target_scope=VketNotice.TargetScope.ALL_PARTICIPANTS,
            created_by=superuser,
        )
        VketNoticeReceipt.objects.create(
            notice=notice, participation=self.participation,
        )

        self.client.force_login(self.owner)
        self._set_active_community()
        response = self.client.get(
            reverse('vket:status', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['unacked_count'], 1)
        self.assertContains(response, '未確認のお知らせ')

    def test_status_page_unacked_count_zero_when_all_acked(self):
        """全レシートがACK済みならバナーは表示されない"""
        superuser = User.objects.create_superuser(
            user_name='su_status2', email='su_status2@example.com', password='p',
        )
        notice = VketNotice.objects.create(
            collaboration=self.collaboration,
            title='確認済み',
            body='本文',
            requires_ack=True,
            target_scope=VketNotice.TargetScope.ALL_PARTICIPANTS,
            created_by=superuser,
        )
        VketNoticeReceipt.objects.create(
            notice=notice,
            participation=self.participation,
            acknowledged_at=timezone.now(),
        )

        self.client.force_login(self.owner)
        self._set_active_community()
        response = self.client.get(
            reverse('vket:status', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['unacked_count'], 0)
        self.assertNotContains(response, '未確認のお知らせ')

    def test_status_page_without_participation(self):
        """participation がない場合 unacked_count=0"""
        other_user = User.objects.create_user(
            user_name='no_part_user', email='nopart@example.com', password='testpass123',
        )
        other_community = Community.objects.create(
            name='別の集会', status='approved', frequency='毎週',
        )
        CommunityMember.objects.create(
            community=other_community,
            user=other_user,
            role=CommunityMember.Role.OWNER,
        )

        self.client.force_login(other_user)
        session = self.client.session
        session['active_community_id'] = other_community.id
        session.save()
        response = self.client.get(
            reverse('vket:status', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['unacked_count'], 0)
        self.assertNotContains(response, '未確認のお知らせ')

    def test_stage_register_advances_progress(self):
        """ステージ登録POSTで progress が STAGE_REGISTERED に進む"""
        self.participation.progress = VketParticipation.Progress.APPLIED
        self.participation.save()

        self.client.force_login(self.owner)
        self._set_active_community()
        response = self.client.post(
            reverse('vket:stage_register', kwargs={'pk': self.collaboration.pk}),
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        self.participation.refresh_from_db()
        self.assertEqual(self.participation.progress, VketParticipation.Progress.STAGE_REGISTERED)
        self.assertIsNotNone(self.participation.stage_registered_at)

    def test_stage_register_requires_applied_participation(self):
        """未申請ではステージ登録できない"""
        self.participation.progress = VketParticipation.Progress.NOT_APPLIED
        self.participation.save()

        self.client.force_login(self.owner)
        self._set_active_community()
        response = self.client.post(
            reverse('vket:stage_register', kwargs={'pk': self.collaboration.pk}),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        self.participation.refresh_from_db()
        self.assertEqual(self.participation.progress, VketParticipation.Progress.NOT_APPLIED)
        self.assertIsNone(self.participation.stage_registered_at)
        self.assertContains(response, 'ステージ登録は参加申込み後に行ってください。')

    def test_stage_register_records_rehearsal_without_rewinding_progress(self):
        """REHEARSAL では進捗を戻さずステージ登録日時だけ記録する"""
        self.participation.progress = VketParticipation.Progress.REHEARSAL
        self.participation.save()

        self.client.force_login(self.owner)
        self._set_active_community()
        response = self.client.post(
            reverse('vket:stage_register', kwargs={'pk': self.collaboration.pk}),
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        self.participation.refresh_from_db()
        self.assertEqual(self.participation.progress, VketParticipation.Progress.REHEARSAL)
        self.assertIsNotNone(self.participation.stage_registered_at)

    def test_stage_register_rejects_after_period_end(self):
        """開催期間終了後はステージ登録日時を記録しない"""
        today = timezone.localdate()
        self.collaboration.period_start = today - timedelta(days=7)
        self.collaboration.period_end = today - timedelta(days=1)
        self.collaboration.save(update_fields=['period_start', 'period_end'])
        self.participation.progress = VketParticipation.Progress.APPLIED
        self.participation.save()

        self.client.force_login(self.owner)
        self._set_active_community()
        response = self.client.post(
            reverse('vket:stage_register', kwargs={'pk': self.collaboration.pk}),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        self.participation.refresh_from_db()
        self.assertEqual(self.participation.progress, VketParticipation.Progress.APPLIED)
        self.assertIsNone(self.participation.stage_registered_at)
        self.assertContains(response, '開催期間終了後はステージ登録を記録できません。')

    def test_stage_register_rejects_duplicate_registration(self):
        """二重登録では既存の登録日時を上書きしない"""
        registered_at = timezone.now() - timedelta(days=1)
        self.participation.progress = VketParticipation.Progress.REHEARSAL
        self.participation.stage_registered_at = registered_at
        self.participation.save()

        self.client.force_login(self.owner)
        self._set_active_community()
        response = self.client.post(
            reverse('vket:stage_register', kwargs={'pk': self.collaboration.pk}),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        self.participation.refresh_from_db()
        self.assertEqual(self.participation.progress, VketParticipation.Progress.REHEARSAL)
        self.assertEqual(self.participation.stage_registered_at, registered_at)
        self.assertContains(response, 'ステージ登録は既に完了しています。')

    def test_status_page_shows_stage_banner(self):
        """申請済み時にステージ登録バナーが表示される"""
        self.participation.progress = VketParticipation.Progress.APPLIED
        self.participation.save()

        self.client.force_login(self.owner)
        self._set_active_community()
        response = self.client.get(
            reverse('vket:status', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Vketステージ登録')

    def test_status_page_hides_stage_register_section_after_registration(self):
        """登録済み後はステージ登録セクションを表示しない"""
        self.participation.progress = VketParticipation.Progress.STAGE_REGISTERED
        self.participation.stage_registered_at = timezone.now()
        self.participation.save()

        self.client.force_login(self.owner)
        self._set_active_community()
        response = self.client.get(
            reverse('vket:status', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['stage_register_open'])
        self.assertNotContains(response, 'Vketステージ登録')
        self.assertNotContains(response, '登録済み（')

    def test_status_page_shows_stage_register_button_for_rehearsal_within_period(self):
        """期間内のREHEARSAL未登録では登録完了ボタンを表示する"""
        self.participation.progress = VketParticipation.Progress.REHEARSAL
        self.participation.save()

        self.client.force_login(self.owner)
        self._set_active_community()
        response = self.client.get(
            reverse('vket:status', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['stage_register_open'])
        self.assertContains(response, '登録完了')

    def test_status_page_hides_stage_register_button_after_period_end(self):
        """期間終了後の未登録は登録ボタンを出さず未登録バッジだけ表示する"""
        today = timezone.localdate()
        self.collaboration.period_start = today - timedelta(days=7)
        self.collaboration.period_end = today - timedelta(days=1)
        self.collaboration.save(update_fields=['period_start', 'period_end'])
        self.participation.progress = VketParticipation.Progress.REHEARSAL
        self.participation.save()

        self.client.force_login(self.owner)
        self._set_active_community()
        response = self.client.get(
            reverse('vket:status', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['stage_register_open'])
        self.assertNotContains(response, '登録完了')
        self.assertContains(response, '未登録')
