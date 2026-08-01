"""Vketコラボ機能のテスト."""


from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse

from community.models import Community
from event.models import EventDetail
from ta_hub.index_cache import get_index_view_cache_key
from vket.models import (
    VketParticipation,
    VketPresentation,
)


User = get_user_model()


from ._vket_test_bases import VketManageViewsBase


class VketManageViewsTests(VketManageViewsBase):
    def test_manage_participation_update_publishes_confirmed_presentation(self):
        """確定ボタンは発表時刻を保存しEventDetailへ公開同期する"""
        self.client.login(username='admin_user', password='adminpass123')
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=Community.objects.create(name='集会C', status='approved', frequency='毎週'),
            confirmed_date=self.collaboration.period_start,
            confirmed_start_time='21:00',
            confirmed_duration=60,
        )
        presentation = VketPresentation.objects.create(
            participation=participation,
            order=0,
            speaker='公開前登壇者',
            theme='公開前テーマ',
            requested_start_time='21:30',
            status=VketPresentation.Status.CONFIRMED,
        )
        new_date = self.collaboration.period_start
        response = self.client.post(
            reverse(
                'vket:manage_participation_update',
                kwargs={
                    'pk': self.collaboration.pk,
                    'participation_id': participation.pk,
                },
            ),
            data={
                'confirmed_date': new_date.isoformat(),
                'confirmed_start_time': '21:00',
                'confirmed_duration': '60',
                'admin_note': '',
                f'pres_{presentation.pk}_start_time': '22:15',
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        presentation.refresh_from_db()
        participation.refresh_from_db()
        self.assertEqual(presentation.confirmed_start_time.strftime('%H:%M'), '22:15')
        self.assertIsNotNone(participation.published_event_id)
        self.assertIsNotNone(presentation.published_event_detail_id)
        self.assertEqual(presentation.published_event_detail.event_id, participation.published_event_id)
        self.assertEqual(presentation.published_event_detail.start_time.strftime('%H:%M'), '22:15')
        self.assertEqual(presentation.published_event_detail.status, 'approved')

    def test_manage_participation_update_updates_published_presentation_start_time(self):
        """公開済み発表の開始時刻はVketPresentationとEventDetailに同期される"""
        self.client.login(username='admin_user', password='adminpass123')
        detail = EventDetail.objects.create(
            event=self.event1,
            detail_type='LT',
            speaker='公開済み登壇者',
            theme='公開済みテーマ',
            start_time='21:30',
            duration=30,
            status='approved',
        )
        presentation = VketPresentation.objects.create(
            participation=self.participation1,
            order=0,
            speaker='公開済み登壇者',
            theme='公開済みテーマ',
            status=VketPresentation.Status.CONFIRMED,
            published_event_detail=detail,
        )
        cache_key = get_index_view_cache_key()
        cache.set(cache_key, {'upcoming_event_details': ['stale']}, 60)
        new_date = self.collaboration.period_start
        response = self.client.post(
            reverse(
                'vket:manage_participation_update',
                kwargs={
                    'pk': self.collaboration.pk,
                    'participation_id': self.participation1.pk,
                },
            ),
            data={
                'confirmed_date': new_date.isoformat(),
                'confirmed_start_time': '21:00',
                'confirmed_duration': '60',
                'admin_note': '',
                f'pres_{presentation.pk}_start_time': '22:15',
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        presentation.refresh_from_db()
        detail.refresh_from_db()
        self.assertEqual(presentation.confirmed_start_time.strftime('%H:%M'), '22:15')
        self.assertEqual(detail.start_time.strftime('%H:%M'), '22:15')
        self.assertIsNone(cache.get(cache_key))

    def test_manage_participation_update_rejects_foreign_presentation(self):
        """別参加の発表開始時刻は更新できない"""
        self.client.login(username='admin_user', password='adminpass123')
        foreign_detail = EventDetail.objects.create(
            event=self.event2,
            detail_type='LT',
            speaker='別参加登壇者',
            theme='別参加テーマ',
            start_time='21:30',
            duration=30,
            status='approved',
        )
        foreign_presentation = VketPresentation.objects.create(
            participation=self.participation2,
            order=0,
            speaker='別参加登壇者',
            theme='別参加テーマ',
            confirmed_start_time='21:30',
            status=VketPresentation.Status.CONFIRMED,
            published_event_detail=foreign_detail,
        )
        new_date = self.collaboration.period_start
        response = self.client.post(
            reverse(
                'vket:manage_participation_update',
                kwargs={
                    'pk': self.collaboration.pk,
                    'participation_id': self.participation1.pk,
                },
            ),
            data={
                'confirmed_date': new_date.isoformat(),
                'confirmed_start_time': '21:00',
                'confirmed_duration': '60',
                'admin_note': '',
                f'pres_{foreign_presentation.pk}_start_time': '23:00',
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        foreign_presentation.refresh_from_db()
        foreign_detail.refresh_from_db()
        self.assertEqual(foreign_presentation.confirmed_start_time.strftime('%H:%M'), '21:30')
        self.assertEqual(foreign_detail.start_time.strftime('%H:%M'), '21:30')

    def test_manage_participation_update_repairs_foreign_event_detail(self):
        """同参加の発表に紐づく別イベントのEventDetailは公開イベントへ付け替える"""
        self.client.login(username='admin_user', password='adminpass123')
        foreign_detail = EventDetail.objects.create(
            event=self.event2,
            detail_type='LT',
            speaker='別イベント登壇者',
            theme='別イベントテーマ',
            start_time='21:30',
            duration=30,
            status='approved',
        )
        presentation = VketPresentation.objects.create(
            participation=self.participation1,
            order=0,
            speaker='不整合登壇者',
            theme='不整合テーマ',
            confirmed_start_time='21:30',
            status=VketPresentation.Status.CONFIRMED,
            published_event_detail=foreign_detail,
        )
        cache_key = get_index_view_cache_key()
        cache.set(cache_key, {'upcoming_event_details': ['stale']}, 60)
        new_date = self.collaboration.period_start
        response = self.client.post(
            reverse(
                'vket:manage_participation_update',
                kwargs={
                    'pk': self.collaboration.pk,
                    'participation_id': self.participation1.pk,
                },
            ),
            data={
                'confirmed_date': new_date.isoformat(),
                'confirmed_start_time': '21:00',
                'confirmed_duration': '60',
                'admin_note': '',
                f'pres_{presentation.pk}_start_time': '23:00',
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        presentation.refresh_from_db()
        foreign_detail.refresh_from_db()
        self.assertEqual(presentation.confirmed_start_time.strftime('%H:%M'), '23:00')
        self.assertEqual(foreign_detail.event_id, self.participation1.published_event_id)
        self.assertEqual(foreign_detail.start_time.strftime('%H:%M'), '23:00')
        self.assertEqual(foreign_detail.status, 'approved')
        self.assertIsNone(cache.get(cache_key))
