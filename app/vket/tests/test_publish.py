"""Vketコラボ機能のテスト."""

from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from community.models import Community
from event.models import Event, EventDetail
from ta_hub.index_cache import get_index_view_cache_key
from vket.models import (
    VketCollaboration,
    VketParticipation,
    VketPresentation,
)


User = get_user_model()


class VketPublishViewTests(TestCase):
    """ManagePublishViewのテスト"""

    def setUp(self):
        self.client = Client()
        cache.clear()
        self.superuser = User.objects.create_superuser(
            user_name='admin_pub',
            email='admin_pub@example.com',
            password='adminpass123',
        )
        self.community = Community.objects.create(
            name='公開テスト集会',
            status='approved',
            frequency='毎週',
        )
        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            slug='vket-2026-publish-test',
            name='公開テスト',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            phase=VketCollaboration.Phase.LOCKED,
        )
        self.participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
            confirmed_date=today,
            confirmed_start_time='21:00',
            confirmed_duration=60,
        )

    def tearDown(self):
        cache.clear()

    def test_publish_creates_event_and_updates_participation(self):
        """公開処理でEventが作成されpublished_eventが紐づく"""
        self.client.login(username='admin_pub', password='adminpass123')
        response = self.client.post(
            reverse('vket:manage_publish', kwargs={'pk': self.collaboration.pk}),
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        self.participation.refresh_from_db()
        self.assertIsNotNone(self.participation.published_event_id)
        self.assertEqual(self.participation.progress, VketParticipation.Progress.DONE)

        event = self.participation.published_event
        self.assertEqual(event.community, self.community)
        self.assertEqual(event.start_time.strftime('%H:%M'), '21:00')
        self.assertEqual(event.duration, 60)
        self.assertEqual(
            event.weekday,
            ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][event.date.weekday()],
        )

    def test_publish_skips_declined_participation(self):
        """不参加の参加情報は公開同期の対象外になる"""
        declined_community = Community.objects.create(
            name='不参加テスト集会',
            status='approved',
            frequency='毎週',
        )
        declined_participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=declined_community,
            lifecycle=VketParticipation.Lifecycle.DECLINED,
            confirmed_date=self.collaboration.period_start,
            confirmed_start_time='22:00',
            confirmed_duration=60,
        )
        VketPresentation.objects.create(
            participation=declined_participation,
            order=0,
            speaker='不参加登壇者',
            theme='不参加テーマ',
            requested_start_time=time(22, 30),
            status=VketPresentation.Status.CONFIRMED,
        )

        self.client.login(username='admin_pub', password='adminpass123')
        response = self.client.post(
            reverse('vket:manage_publish', kwargs={'pk': self.collaboration.pk}),
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        declined_participation.refresh_from_db()
        self.assertIsNone(declined_participation.published_event_id)
        self.assertFalse(Event.objects.filter(community=declined_community).exists())
        self.assertFalse(EventDetail.objects.filter(speaker='不参加登壇者').exists())

    def test_publish_is_forbidden_when_not_locked(self):
        """LOCKEDフェーズ以外では公開処理が403になる"""
        self.collaboration.phase = VketCollaboration.Phase.SCHEDULING
        self.collaboration.save()

        self.client.login(username='admin_pub', password='adminpass123')
        response = self.client.post(
            reverse('vket:manage_publish', kwargs={'pk': self.collaboration.pk}),
        )
        self.assertEqual(response.status_code, 403)

    def test_publish_is_idempotent(self):
        """公開処理を2回実行しても同じEventが使われ、重複作成されない"""
        from event.models import Event

        self.client.login(username='admin_pub', password='adminpass123')
        url = reverse('vket:manage_publish', kwargs={'pk': self.collaboration.pk})

        # 1回目
        self.client.post(url)
        self.participation.refresh_from_db()
        first_event_id = self.participation.published_event_id
        self.assertIsNotNone(first_event_id)

        # 2回目
        self.client.post(url)
        self.participation.refresh_from_db()
        # 同じEventが使われていること
        self.assertEqual(self.participation.published_event_id, first_event_id)
        # Eventが重複作成されていないこと
        self.assertEqual(Event.objects.filter(community=self.community).count(), 1)

    def test_lt_start_time_flows_to_event_detail(self):
        """requested_start_time が EventDetail.start_time に反映される"""
        # プレゼンテーションに requested_start_time を設定（CONFIRMED で公開対象にする）
        VketPresentation.objects.create(
            participation=self.participation,
            order=0,
            speaker='テスト登壇者',
            theme='テストテーマ',
            requested_start_time=time(21, 30),
            status=VketPresentation.Status.CONFIRMED,
        )

        self.client.login(username='admin_pub', password='adminpass123')
        response = self.client.post(
            reverse('vket:manage_publish', kwargs={'pk': self.collaboration.pk}),
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        self.participation.refresh_from_db()
        event = self.participation.published_event
        self.assertIsNotNone(event)

        # EventDetail が作成され、start_time に requested_start_time が使われていること
        detail = EventDetail.objects.get(event=event)
        self.assertEqual(detail.start_time.strftime('%H:%M'), '21:30')

    def test_publish_skips_draft_presentation(self):
        """申請中の発表は公開処理でEventDetailにしない"""
        VketPresentation.objects.create(
            participation=self.participation,
            order=0,
            speaker='申請中登壇者',
            theme='申請中テーマ',
            requested_start_time=time(21, 30),
            status=VketPresentation.Status.DRAFT,
        )

        self.client.login(username='admin_pub', password='adminpass123')
        response = self.client.post(
            reverse('vket:manage_publish', kwargs={'pk': self.collaboration.pk}),
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        self.participation.refresh_from_db()
        self.assertIsNotNone(self.participation.published_event_id)
        self.assertFalse(EventDetail.objects.filter(speaker='申請中登壇者').exists())

    def test_publish_clears_index_cache_when_updating_existing_event_detail(self):
        """公開処理で既存EventDetailをbulk updateした場合もトップページキャッシュを削除する"""
        event = Event.objects.create(
            community=self.community,
            date=self.collaboration.period_start,
            start_time='21:00',
            duration=60,
            weekday='Fri',
        )
        self.participation.published_event = event
        self.participation.save(update_fields=['published_event', 'updated_at'])
        detail = EventDetail.objects.create(
            event=event,
            detail_type='LT',
            speaker='更新前登壇者',
            theme='更新前テーマ',
            start_time='21:00',
            status='approved',
        )
        VketPresentation.objects.create(
            participation=self.participation,
            order=0,
            speaker='更新後登壇者',
            theme='更新後テーマ',
            requested_start_time=time(21, 30),
            status=VketPresentation.Status.CONFIRMED,
            published_event_detail=detail,
        )
        cache_key = get_index_view_cache_key()
        cache.set(cache_key, {'upcoming_event_details': ['stale']}, 60)

        self.client.login(username='admin_pub', password='adminpass123')
        response = self.client.post(
            reverse('vket:manage_publish', kwargs={'pk': self.collaboration.pk}),
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        detail.refresh_from_db()
        self.assertEqual(detail.speaker, '更新後登壇者')
        self.assertEqual(detail.start_time.strftime('%H:%M'), '21:30')
        self.assertIsNone(cache.get(cache_key))
