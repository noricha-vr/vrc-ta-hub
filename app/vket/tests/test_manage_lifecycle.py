"""Vketコラボ機能のテスト."""


from django.contrib.auth import get_user_model
from django.urls import reverse

from community.models import Community
from event.models import Event, EventDetail
from vket.models import (
    VketParticipation,
    VketPresentation,
)


User = get_user_model()


from ._vket_test_bases import VketManageViewsBase


class VketManageViewsTests(VketManageViewsBase):
    def test_manage_participation_update_sets_lifecycle_without_schedule(self):
        """管理画面から参加状態だけを不参加に変更できる"""
        self.client.force_login(self.superuser)
        new_participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=Community.objects.create(name='集会C', status='approved', frequency='毎週'),
            requested_date=self.collaboration.period_start,
            requested_start_time='22:00',
            requested_duration=60,
        )
        response = self.client.post(
            reverse(
                'vket:manage_participation_update',
                kwargs={
                    'pk': self.collaboration.pk,
                    'participation_id': new_participation.pk,
                },
            ),
            data={
                'lifecycle': VketParticipation.Lifecycle.DECLINED,
                'admin_note': '今回は不参加',
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        new_participation.refresh_from_db()
        self.assertEqual(new_participation.lifecycle, VketParticipation.Lifecycle.DECLINED)
        self.assertEqual(new_participation.admin_note, '今回は不参加')
        self.assertFalse(new_participation.schedule_adjusted_by_admin)
        self.assertIsNone(new_participation.schedule_confirmed_at)

    def test_manage_participation_update_declined_clears_published_event(self):
        """公開済み参加を不参加にすると公開イベント連携を解除する"""
        self.client.force_login(self.superuser)
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

        response = self.client.post(
            reverse(
                'vket:manage_participation_update',
                kwargs={
                    'pk': self.collaboration.pk,
                    'participation_id': self.participation1.pk,
                },
            ),
            data={
                'lifecycle': VketParticipation.Lifecycle.DECLINED,
                'admin_note': '公開後に不参加',
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        self.participation1.refresh_from_db()
        presentation.refresh_from_db()
        self.assertEqual(self.participation1.lifecycle, VketParticipation.Lifecycle.DECLINED)
        self.assertIsNone(self.participation1.published_event_id)
        self.assertIsNone(presentation.published_event_detail_id)
        self.assertTrue(Event.objects.filter(pk=self.event1.pk).exists())
        self.assertFalse(EventDetail.objects.filter(pk=detail.pk).exists())

    def test_manage_participation_update_requires_schedule_for_active(self):
        """参加中で保存する場合は確定日程が必須"""
        self.client.force_login(self.superuser)
        new_participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=Community.objects.create(name='集会C', status='approved', frequency='毎週'),
        )
        response = self.client.post(
            reverse(
                'vket:manage_participation_update',
                kwargs={
                    'pk': self.collaboration.pk,
                    'participation_id': new_participation.pk,
                },
            ),
            data={
                'lifecycle': VketParticipation.Lifecycle.ACTIVE,
                'admin_note': '日程なし',
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        new_participation.refresh_from_db()
        self.assertIsNone(new_participation.confirmed_date)
        self.assertFalse(new_participation.schedule_adjusted_by_admin)
