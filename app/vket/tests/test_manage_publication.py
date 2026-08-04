"""Vketコラボ機能のテスト."""

from datetime import timedelta
from importlib import import_module

from django.contrib.auth import get_user_model
from django.apps import apps
from django.urls import reverse

from community.models import Community
from event.models import Event, EventDetail, EventOccurrenceTombstone
from vket.models import (
    VketParticipation,
    VketPresentation,
)
from vket.services import sync_participation_publication


User = get_user_model()


from ._vket_test_bases import VketManageViewsBase


class VketManageViewsTests(VketManageViewsBase):
    def test_manage_view_shows_active_publication_date_drift(self):
        """管理画面は参加中の公開イベントと確定日の差分を表示する"""
        changed_date = self.collaboration.period_start + timedelta(days=1)
        self.event1.date = changed_date
        self.event1.save(update_fields=['date'])
        self.client.login(username='admin_user', password='adminpass123')

        response = self.client.get(
            reverse('vket:manage', kwargs={'pk': self.collaboration.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [p.pk for p in response.context['publication_drift_participations']],
            [self.participation1.pk],
        )
        self.assertContains(response, 'data-testid="publication-drift-warning"')
        self.assertContains(
            response,
            f'data-participation-id="{self.participation1.pk}"',
        )
        self.assertContains(response, changed_date.strftime('%Y/%m/%d'))
        self.assertContains(
            response,
            self.participation1.confirmed_date.strftime('%Y/%m/%d'),
        )

    def test_manage_view_shows_active_publication_start_time_drift(self):
        """管理画面は参加中の公開イベントと確定開始時刻の差分を表示する"""
        self.event1.start_time = '22:00'
        self.event1.save(update_fields=['start_time'])
        self.client.login(username='admin_user', password='adminpass123')

        response = self.client.get(
            reverse('vket:manage', kwargs={'pk': self.collaboration.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [p.pk for p in response.context['publication_drift_participations']],
            [self.participation1.pk],
        )
        self.assertContains(response, 'data-testid="publication-drift-warning"')
        self.assertContains(response, '21:00')
        self.assertContains(response, '22:00')

    def test_manage_view_shows_active_publication_duration_drift(self):
        """管理画面は公開イベントと確定開催時間の差分を表示する"""
        self.event1.duration = 90
        self.event1.save(update_fields=['duration'])
        self.client.login(username='admin_user', password='adminpass123')

        response = self.client.get(
            reverse('vket:manage', kwargs={'pk': self.collaboration.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [p.pk for p in response.context['publication_drift_participations']],
            [self.participation1.pk],
        )
        self.assertRegex(
            response.content.decode(),
            (
                rf'(?s)<tr data-participation-id="{self.participation1.pk}">.*?'
                r'確定: .*?21:00（60分）.*?'
                r'公開: .*?21:00（90分）.*?</tr>'
            ),
        )

    def test_manage_view_ignores_non_active_participations(self):
        """管理画面は未関連Eventがない不参加を警告しない"""
        self.participation1.published_event = None
        self.participation1.confirmed_date = (
            self.collaboration.period_start + timedelta(days=2)
        )
        self.participation1.lifecycle = VketParticipation.Lifecycle.DECLINED
        self.participation1.save(
            update_fields=[
                'published_event',
                'confirmed_date',
                'lifecycle',
                'updated_at',
            ]
        )
        self.client.login(username='admin_user', password='adminpass123')

        response = self.client.get(
            reverse('vket:manage', kwargs={'pk': self.collaboration.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['publication_drift_participations'], [])
        self.assertEqual(
            response.context['publication_missing_event_participations'],
            [],
        )
        self.assertNotContains(response, 'data-testid="publication-drift-warning"')

    def test_manage_view_warns_when_unpublished_confirmed_event_is_missing(self):
        """管理画面は未関連で確定日時のEventがない参加を警告する"""
        missing_date = self.collaboration.period_start + timedelta(days=2)
        self.participation2.published_event = None
        self.participation2.confirmed_date = missing_date
        self.participation2.save(
            update_fields=['published_event', 'confirmed_date', 'updated_at']
        )
        self.client.login(username='admin_user', password='adminpass123')

        response = self.client.get(
            reverse('vket:manage', kwargs={'pk': self.collaboration.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="publication-drift-warning"')
        self.assertContains(
            response,
            f'data-participation-id="{self.participation2.pk}"',
        )
        self.assertContains(response, missing_date.strftime('%Y/%m/%d'))
        self.assertContains(response, '21:30')
        self.assertContains(response, 'ロックが機能していません')

    def test_manage_view_ignores_unpublished_participation_with_matching_event(self):
        """管理画面は確定日時に一致するEventがある未関連参加を警告しない"""
        self.participation2.published_event = None
        self.participation2.save(update_fields=['published_event', 'updated_at'])
        self.client.login(username='admin_user', password='adminpass123')

        response = self.client.get(
            reverse('vket:manage', kwargs={'pk': self.collaboration.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="publication-drift-warning"')

    def test_manage_view_requires_matching_event_in_same_community(self):
        """管理画面は別集会にだけ一致Eventがある未関連参加を警告する"""
        self.participation1.published_event = None
        self.participation1.confirmed_start_time = self.event2.start_time
        self.participation1.save(
            update_fields=[
                'published_event',
                'confirmed_start_time',
                'updated_at',
            ]
        )
        self.client.login(username='admin_user', password='adminpass123')

        response = self.client.get(
            reverse('vket:manage', kwargs={'pk': self.collaboration.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [
                p.pk
                for p in response.context[
                    'publication_missing_event_participations'
                ]
            ],
            [self.participation1.pk],
        )
        self.assertContains(
            response,
            f'data-participation-id="{self.participation1.pk}"',
        )

    def test_manage_view_ignores_unpublished_participation_without_confirmed_schedule(self):
        """管理画面は確定日時の片方が未設定の未関連参加を警告しない"""
        confirmed_date = self.participation2.confirmed_date
        confirmed_start_time = self.participation2.confirmed_start_time
        self.participation2.published_event = None
        self.client.login(username='admin_user', password='adminpass123')

        for missing_field in ('confirmed_date', 'confirmed_start_time'):
            with self.subTest(missing_field=missing_field):
                self.participation2.confirmed_date = confirmed_date
                self.participation2.confirmed_start_time = confirmed_start_time
                setattr(self.participation2, missing_field, None)
                self.participation2.save(
                    update_fields=[
                        'published_event',
                        'confirmed_date',
                        'confirmed_start_time',
                        'updated_at',
                    ]
                )

                response = self.client.get(
                    reverse('vket:manage', kwargs={'pk': self.collaboration.pk})
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.context['publication_missing_event_participations'],
                    [],
                )
                self.assertNotContains(
                    response,
                    'data-testid="publication-drift-warning"',
                )

    def test_manage_participation_update_sets_confirmed_fields(self):
        """ManageParticipationUpdateViewが確定日程・progressを正しくセットする"""
        self.client.force_login(self.superuser)
        # 未確定の参加を作成してテスト
        new_participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=Community.objects.create(name='集会C', status='approved', frequency='毎週'),
            requested_date=self.collaboration.period_start,
            requested_start_time='22:00',
            requested_duration=60,
        )
        new_date = self.collaboration.period_start + timedelta(days=1)
        response = self.client.post(
            reverse(
                'vket:manage_participation_update',
                kwargs={
                    'pk': self.collaboration.pk,
                    'participation_id': new_participation.pk,
                },
            ),
            data={
                'confirmed_date': new_date.isoformat(),
                'confirmed_start_time': '22:00',
                'confirmed_duration': '60',
                'admin_note': '確定しました',
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        new_participation.refresh_from_db()
        self.assertEqual(new_participation.confirmed_date, new_date)
        self.assertEqual(new_participation.confirmed_start_time.strftime('%H:%M'), '22:00')
        self.assertEqual(new_participation.confirmed_duration, 60)
        self.assertEqual(new_participation.admin_note, '確定しました')
        self.assertTrue(new_participation.schedule_adjusted_by_admin)
        self.assertEqual(new_participation.progress, VketParticipation.Progress.REHEARSAL)
        self.assertIsNotNone(new_participation.schedule_confirmed_at)
        self.assertIsNotNone(new_participation.published_event_id)

    def test_manage_participation_update_reuses_existing_event(self):
        """確定日程に一致する既存Eventがあれば公開イベントとして再利用する"""
        self.client.force_login(self.superuser)
        community = Community.objects.create(name='集会C', status='approved', frequency='毎週')
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=community,
            requested_date=self.collaboration.period_start,
            requested_start_time='22:00',
            requested_duration=60,
        )
        existing_event = Event.objects.create(
            community=community,
            date=self.collaboration.period_start,
            start_time='22:00',
            duration=60,
            weekday='Tue',
        )
        presentation = VketPresentation.objects.create(
            participation=participation,
            order=0,
            speaker='既存Event登壇者',
            theme='既存Eventテーマ',
            requested_start_time='22:30',
            status=VketPresentation.Status.CONFIRMED,
        )

        response = self.client.post(
            reverse(
                'vket:manage_participation_update',
                kwargs={
                    'pk': self.collaboration.pk,
                    'participation_id': participation.pk,
                },
            ),
            data={
                'confirmed_date': self.collaboration.period_start.isoformat(),
                'confirmed_start_time': '22:00',
                'confirmed_duration': '60',
            },
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        participation.refresh_from_db()
        presentation.refresh_from_db()
        self.assertEqual(participation.published_event_id, existing_event.pk)
        self.assertEqual(Event.objects.filter(community=community).count(), 1)
        self.assertEqual(presentation.published_event_detail.event_id, existing_event.pk)
        self.assertEqual(presentation.published_event_detail.start_time.strftime('%H:%M'), '22:30')

    def test_publication_sync_updates_duration_on_related_existing_event(self):
        """公開同期は関連済みEventの開催時間差分を更新する。"""
        self.event1.duration = 90
        self.event1.save(update_fields=['duration'])

        result = sync_participation_publication(self.participation1)

        self.event1.refresh_from_db()
        self.assertEqual(self.event1.duration, self.participation1.confirmed_duration)
        self.assertEqual(result.event.pk, self.event1.pk)
        self.assertTrue(result.changed_index_data)

    def test_publication_sync_date_update_does_not_create_tombstone(self):
        """Vket運営同期の日付更新はユーザー例外として記録しない"""
        new_date = self.collaboration.period_start + timedelta(days=1)
        self.participation1.confirmed_date = new_date
        self.participation1.save(update_fields=['confirmed_date'])

        sync_participation_publication(self.participation1)

        self.event1.refresh_from_db()
        self.assertEqual(self.event1.date, new_date)
        self.assertEqual(
            self.event1.weekday,
            ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][new_date.weekday()],
        )
        self.assertFalse(
            EventOccurrenceTombstone.objects.filter(
                community=self.community1,
            ).exists()
        )

    def test_sync_participation_publication_sets_applicant_on_new_detail(self):
        """公開同期で新規EventDetailへVket申請者を設定する。"""
        self.participation1.applied_by = self.normal_user
        self.participation1.save(update_fields=['applied_by'])
        presentation = VketPresentation.objects.create(
            participation=self.participation1,
            order=0,
            speaker='Vket発表者',
            theme='Vketテーマ',
            status=VketPresentation.Status.CONFIRMED,
        )

        sync_participation_publication(self.participation1)

        presentation.refresh_from_db()
        self.assertEqual(presentation.published_event_detail.applicant, self.normal_user)

    def test_sync_participation_publication_backfills_applicant_on_existing_detail(self):
        """再同期で既存EventDetailの未設定申請者を補完する。"""
        self.participation1.applied_by = self.normal_user
        self.participation1.save(update_fields=['applied_by'])
        detail = EventDetail.objects.create(
            event=self.event1,
            detail_type='LT',
            speaker='Vket発表者',
            theme='Vketテーマ',
            start_time='21:00',
            duration=30,
            status='approved',
        )
        VketPresentation.objects.create(
            participation=self.participation1,
            order=0,
            speaker='Vket発表者',
            theme='Vketテーマ',
            status=VketPresentation.Status.CONFIRMED,
            published_event_detail=detail,
        )

        sync_participation_publication(self.participation1)

        detail.refresh_from_db()
        self.assertEqual(detail.applicant, self.normal_user)

    def test_sync_participation_publication_keeps_applicant_when_applied_by_is_none(self):
        """Vket申請者が未設定でも既存EventDetailの申請者を保持する。"""
        detail = EventDetail.objects.create(
            event=self.event1,
            detail_type='LT',
            speaker='既存発表者',
            theme='既存テーマ',
            start_time='21:00',
            duration=30,
            status='approved',
            applicant=self.normal_user,
        )
        VketPresentation.objects.create(
            participation=self.participation1,
            order=0,
            speaker='既存発表者',
            theme='既存テーマ',
            status=VketPresentation.Status.CONFIRMED,
            published_event_detail=detail,
        )

        sync_participation_publication(self.participation1)

        detail.refresh_from_db()
        self.assertEqual(detail.applicant, self.normal_user)

    def test_vket_applicant_backfill_uses_first_ordered_presentation(self):
        """移行処理は表示順が先のVket発表に紐づく申請者を設定する。"""
        detail = EventDetail.objects.create(
            event=self.event1,
            detail_type='LT',
            speaker='移行対象',
            theme='移行テーマ',
            start_time='21:00',
            duration=30,
            status='approved',
        )
        self.participation1.applied_by = self.normal_user
        self.participation1.save(update_fields=['applied_by'])
        self.participation2.applied_by = self.superuser
        self.participation2.save(update_fields=['applied_by'])
        VketPresentation.objects.create(
            participation=self.participation1,
            published_event_detail=detail,
            order=1,
        )
        VketPresentation.objects.create(
            participation=self.participation2,
            published_event_detail=detail,
            order=0,
        )

        migration = import_module('vket.migrations.0010_backfill_vket_event_detail_applicant')
        migration.backfill_vket_event_detail_applicant(apps, None)

        detail.refresh_from_db()
        self.assertEqual(detail.applicant, self.superuser)

    def test_manage_participation_update_approves_existing_pending_detail(self):
        """既存のpending EventDetailは確定時にapprovedへ同期される"""
        self.client.force_login(self.superuser)
        detail = EventDetail.objects.create(
            event=self.event1,
            detail_type='LT',
            speaker='承認前登壇者',
            theme='承認前テーマ',
            start_time='21:30',
            duration=30,
            status='pending',
        )
        presentation = VketPresentation.objects.create(
            participation=self.participation1,
            order=0,
            speaker='承認後登壇者',
            theme='承認後テーマ',
            requested_start_time='21:45',
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
                'confirmed_date': self.collaboration.period_start.isoformat(),
                'confirmed_start_time': '21:00',
                'confirmed_duration': '60',
            },
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        presentation.refresh_from_db()
        detail.refresh_from_db()
        self.assertEqual(detail.status, 'approved')
        self.assertEqual(detail.speaker, '承認後登壇者')
        self.assertEqual(detail.theme, '承認後テーマ')
        self.assertEqual(detail.start_time.strftime('%H:%M'), '21:45')
        self.assertEqual(presentation.published_event_detail_id, detail.pk)
