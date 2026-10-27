"""Vketコラボ機能のテスト."""


from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.urls import reverse

from community.models import Community
from event.models import Event, EventDetail
from tests.factories import make_event_detail
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
                'confirm_withdraw': '1',
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
                'confirm_withdraw': '1',
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


class VketParticipationConfirmationTests(VketManageViewsBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.superuser)
        self.detail = make_event_detail(self.event1, status='approved')
        self.presentation = VketPresentation.objects.create(
            participation=self.participation1,
            speaker='公開済み登壇者',
            status=VketPresentation.Status.CONFIRMED,
            published_event_detail=self.detail,
        )
        self.update_url = reverse('vket:manage_participation_update', kwargs={
            'pk': self.collaboration.pk,
            'participation_id': self.participation1.pk,
        })
        self.manage_url = reverse('vket:manage', kwargs={'pk': self.collaboration.pk})

    def test_leaving_active_without_confirmation_preserves_all_data(self):
        """不参加・辞退への変更は確認値がなければ公開済みの発表も含め何も変えない"""
        original_updated_at = self.participation1.updated_at
        for lifecycle in (VketParticipation.Lifecycle.DECLINED, VketParticipation.Lifecycle.WITHDRAWN):
            for confirmation in ({}, {'confirm_withdraw': '0'}):
                with self.subTest(lifecycle=lifecycle, confirmation=confirmation):
                    response = self.client.post(self.update_url, {
                        'lifecycle': lifecycle,
                        'admin_note': '変更されない備考',
                        'confirmed_date': self.collaboration.period_end.isoformat(),
                        'confirmed_start_time': '23:00',
                        'confirmed_duration': '30',
                        f'pres_{self.presentation.pk}_start_time': '23:15',
                        **confirmation,
                    }, follow=True)
                    self.assertRedirects(response, self.manage_url, fetch_redirect_response=False)
                    errors = list(response.context['messages'])
                    self.assertEqual(len(errors), 1)
                    self.assertEqual(errors[0].level, messages.ERROR)
                    self.assertIn('確認にチェックしてください', str(errors[0]))
                    self.participation1.refresh_from_db()
                    self.presentation.refresh_from_db()
                    self.event1.refresh_from_db()
                    self.detail.refresh_from_db()
                    self.assertEqual(self.participation1.lifecycle, VketParticipation.Lifecycle.ACTIVE)
                    self.assertEqual(self.participation1.admin_note, '')
                    self.assertEqual(self.participation1.confirmed_date, self.collaboration.period_start)
                    self.assertEqual(self.participation1.updated_at, original_updated_at)
                    self.assertEqual(self.participation1.published_event_id, self.event1.pk)
                    self.assertEqual(self.presentation.published_event_detail_id, self.detail.pk)
                    self.assertIsNone(self.presentation.confirmed_start_time)
                    self.assertEqual(self.event1.date, self.collaboration.period_start)
                    self.assertEqual(self.detail.status, 'approved')

    def test_withdrawal_with_confirmation_clears_publication(self):
        """確認済みの辞退は公開発表を削除し、イベント連携を外す"""
        response = self.client.post(self.update_url, {
            'lifecycle': VketParticipation.Lifecycle.WITHDRAWN,
            'admin_note': '確認済みの辞退',
            'confirm_withdraw': '1',
        })
        self.assertRedirects(response, self.manage_url, fetch_redirect_response=False)
        self.participation1.refresh_from_db()
        self.presentation.refresh_from_db()
        self.assertEqual(self.participation1.lifecycle, VketParticipation.Lifecycle.WITHDRAWN)
        self.assertEqual(self.participation1.admin_note, '確認済みの辞退')
        self.assertIsNone(self.participation1.published_event_id)
        self.assertIsNone(self.presentation.published_event_detail_id)
        self.assertFalse(EventDetail.objects.filter(pk=self.detail.pk).exists())
        self.assertTrue(Event.objects.filter(pk=self.event1.pk).exists())

    def test_active_schedule_update_does_not_require_confirmation(self):
        """参加中のまま日程を変える時は確認なしで公開イベントも更新できる"""
        new_date = self.collaboration.period_start + timedelta(days=1)
        response = self.client.post(self.update_url, {
            'lifecycle': VketParticipation.Lifecycle.ACTIVE,
            'confirmed_date': new_date.isoformat(),
            'confirmed_start_time': '22:00',
            'confirmed_duration': '60',
            'admin_note': '日程変更',
        })
        self.assertRedirects(response, self.manage_url, fetch_redirect_response=False)
        self.participation1.refresh_from_db()
        self.event1.refresh_from_db()
        self.presentation.refresh_from_db()
        self.assertEqual(self.participation1.lifecycle, VketParticipation.Lifecycle.ACTIVE)
        self.assertEqual(self.participation1.confirmed_date, new_date)
        self.assertEqual(self.participation1.confirmed_start_time.strftime('%H:%M'), '22:00')
        self.assertEqual(self.participation1.admin_note, '日程変更')
        self.assertEqual(self.participation1.published_event_id, self.event1.pk)
        self.assertEqual(self.event1.date, new_date)
        self.assertEqual(self.presentation.published_event_detail_id, self.detail.pk)
        self.assertTrue(EventDetail.objects.filter(pk=self.detail.pk).exists())

    def test_inactive_note_update_does_not_require_confirmation(self):
        """既に不参加・辞退の参加は確認なしで備考を更新できる"""
        for lifecycle in (VketParticipation.Lifecycle.DECLINED, VketParticipation.Lifecycle.WITHDRAWN):
            with self.subTest(lifecycle=lifecycle):
                self.participation1.lifecycle = lifecycle
                self.participation1.save(update_fields=['lifecycle'])
                response = self.client.post(self.update_url, {
                    'lifecycle': lifecycle,
                    'admin_note': '備考の更新',
                })
                self.assertRedirects(response, self.manage_url, fetch_redirect_response=False)
                self.participation1.refresh_from_db()
                self.assertEqual(self.participation1.lifecycle, lifecycle)
                self.assertEqual(self.participation1.admin_note, '備考の更新')
                self.assertEqual(list(get_messages(response.wsgi_request))[-1].level, messages.SUCCESS)
