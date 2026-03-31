"""Vketコラボ期間中の日時変更・削除ロックのテスト（参照: PR #138）"""

from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from community.models import Community, CommunityMember
from event.models import Event, EventDetail
from vket.models import VketCollaboration, VketParticipation
from vket.services import get_vket_lock_info, is_event_locked_by_vket, get_vket_lock_message

User = get_user_model()


class VketScheduleLockServiceTests(TestCase):
    """is_event_locked_by_vket / get_vket_lock_message のユニットテスト"""

    def setUp(self):
        self.community = Community.objects.create(
            name='テスト集会', status='approved', frequency='毎週',
        )
        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            slug='lock-test',
            name='Vket Lock Test',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today,
            lt_deadline=today + timedelta(days=3),
        )
        self.event_in_period = Event.objects.create(
            community=self.community,
            date=today + timedelta(days=1),
            start_time='22:00',
            duration=60,
        )
        self.event_outside_period = Event.objects.create(
            community=self.community,
            date=today + timedelta(days=30),
            start_time='22:00',
            duration=60,
        )

    def test_no_participation_not_locked(self):
        """参加していないCommunityのイベントはロックされない"""
        self.assertFalse(is_event_locked_by_vket(self.event_in_period))

    def test_active_participation_in_period_locked(self):
        """アクティブな参加があり期間内のイベントはロックされる"""
        VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
        )
        self.assertTrue(is_event_locked_by_vket(self.event_in_period))

    def test_active_participation_outside_period_not_locked(self):
        """アクティブな参加があっても期間外のイベントはロックされない"""
        VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
        )
        self.assertFalse(is_event_locked_by_vket(self.event_outside_period))

    def test_declined_participation_not_locked(self):
        """不参加の場合はロックされない"""
        VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            lifecycle=VketParticipation.Lifecycle.DECLINED,
        )
        self.assertFalse(is_event_locked_by_vket(self.event_in_period))

    def test_lock_message_contains_collab_name(self):
        """ロックメッセージにコラボ名が含まれる"""
        VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
        )
        msg = get_vket_lock_message(self.event_in_period)
        self.assertIn('Vket Lock Test', msg)
        self.assertIn('運営のみ', msg)

    def test_lock_message_empty_when_not_locked(self):
        """ロックされていない場合は空文字列"""
        msg = get_vket_lock_message(self.event_in_period)
        self.assertEqual(msg, "")


class VketScheduleLockViewTests(TestCase):
    """ビューレベルのロックテスト"""

    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(
            user_name='owner_user', email='owner@example.com', password='testpass123',
        )
        self.superuser = User.objects.create_superuser(
            user_name='admin_user', email='admin@example.com', password='adminpass123',
        )
        self.community = Community.objects.create(
            name='ロック集会', status='approved', frequency='毎週',
        )
        CommunityMember.objects.create(
            user=self.owner, community=self.community, role=CommunityMember.Role.OWNER,
        )
        CommunityMember.objects.create(
            user=self.superuser, community=self.community, role=CommunityMember.Role.OWNER,
        )

        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            slug='lock-view-test',
            name='Vket Lock View',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today,
            lt_deadline=today + timedelta(days=3),
        )
        self.event = Event.objects.create(
            community=self.community,
            date=today + timedelta(days=1),
            start_time='22:00',
            duration=60,
        )
        self.participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
        )
        self.detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            start_time='22:00',
            duration=30,
            speaker='テスト発表者',
            theme='テストテーマ',
        )

    def _login_as_owner(self):
        self.client.login(username='owner_user', password='testpass123')
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

    def _login_as_superuser(self):
        self.client.login(username='admin_user', password='adminpass123')
        session = self.client.session
        session['active_community_id'] = self.community.id
        session.save()

    def test_event_detail_update_locks_time_fields(self):
        """集会管理者のEventDetail編集で日時フィールドがロックされる"""
        self._login_as_owner()
        url = reverse('event:detail_update', kwargs={'pk': self.detail.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '運営のみ')
        self.assertContains(response, 'fa-lock')

    def test_event_detail_update_keeps_time_on_locked_post(self):
        """ロック中にPOSTしても日時フィールドは元の値が維持される"""
        self._login_as_owner()
        url = reverse('event:detail_update', kwargs={'pk': self.detail.pk})
        response = self.client.post(url, {
            'detail_type': 'LT',
            'theme': 'テストテーマ更新',
            'speaker': 'テスト発表者更新',
            # start_time と duration は disabled のため送信されない
        })
        self.detail.refresh_from_db()
        # テーマと発表者は更新される
        self.assertEqual(self.detail.theme, 'テストテーマ更新')
        self.assertEqual(self.detail.speaker, 'テスト発表者更新')
        # 日時は変更されない
        self.assertEqual(self.detail.start_time, time(22, 0))
        self.assertEqual(self.detail.duration, 30)

    def test_superuser_can_update_time_fields(self):
        """superuserはロック中でも日時を変更できる"""
        self._login_as_superuser()
        url = reverse('event:detail_update', kwargs={'pk': self.detail.pk})
        response = self.client.post(url, {
            'detail_type': 'LT',
            'theme': 'テストテーマ',
            'speaker': 'テスト発表者',
            'start_time': '23:00',
            'duration': 45,
        })
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.start_time, time(23, 0))
        self.assertEqual(self.detail.duration, 45)

    def test_event_delete_blocked_during_vket(self):
        """集会管理者はVket期間中のイベントを削除できない"""
        self._login_as_owner()
        url = reverse('event:delete', kwargs={'pk': self.event.pk})
        response = self.client.post(url)
        # イベントがまだ存在することを確認
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())

    def test_superuser_can_delete_event_during_vket(self):
        """superuserはVket期間中でもイベントを削除できる"""
        self._login_as_superuser()
        url = reverse('event:delete', kwargs={'pk': self.event.pk})
        response = self.client.post(url)
        self.assertFalse(Event.objects.filter(pk=self.event.pk).exists())

    def test_event_detail_delete_blocked_during_vket(self):
        """集会管理者はVket期間中のEventDetailを削除できない"""
        self._login_as_owner()
        url = reverse('event:detail_delete', kwargs={'pk': self.detail.pk})
        response = self.client.post(url)
        # EventDetailがまだ存在することを確認
        self.assertTrue(EventDetail.objects.filter(pk=self.detail.pk).exists())

    def test_superuser_can_delete_event_detail_during_vket(self):
        """superuserはVket期間中でもEventDetailを削除できる"""
        self._login_as_superuser()
        url = reverse('event:detail_delete', kwargs={'pk': self.detail.pk})
        response = self.client.post(url)
        self.assertFalse(EventDetail.objects.filter(pk=self.detail.pk).exists())

    def test_no_lock_without_vket_participation(self):
        """Vket参加がないCommunityのイベントはロックされない"""
        self.participation.delete()
        self._login_as_owner()
        url = reverse('event:detail_update', kwargs={'pk': self.detail.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'fa-lock')
