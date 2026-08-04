"""Vketコラボ機能のテスト."""

from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from event.models import Event, EventDetail
from vket.models import (
    VketParticipation,
    VketPresentation,
)


User = get_user_model()


from ._vket_test_bases import VketApplyFlowBase


class VketApplyFlowTests(VketApplyFlowBase):
    def test_confirmed_participation_post_allows_unlocked_lt_start_time(self):
        """日程確定後も締切内なら未確定LTの開始時刻を更新できる"""
        self.client.force_login(self.owner)
        self._set_active_community()

        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            requested_date=self.collaboration.period_start,
            requested_start_time=time(21, 0),
            requested_duration=60,
            confirmed_date=self.collaboration.period_start,
            confirmed_start_time=time(21, 0),
            confirmed_duration=60,
            schedule_confirmed_at=timezone.now(),
            progress=VketParticipation.Progress.REHEARSAL,
            applied_by=self.owner,
        )
        presentation = VketPresentation.objects.create(
            participation=participation,
            order=0,
            speaker='確定前登壇者',
            theme='確定前テーマ',
            requested_start_time=time(21, 30),
            status=VketPresentation.Status.DRAFT,
        )

        post_data = {
            'requested_date': (self.collaboration.period_start + timedelta(days=1)).isoformat(),
            'requested_start_time': '23:00',
            'requested_duration': '90',
            'organizer_note': '確定後も備考は更新',
            'lt_slot_minutes': '20',
        }
        post_data.update(
            self._make_formset_data(
                [
                    {
                        'speaker': '更新後登壇者',
                        'theme': '更新後テーマ',
                        'lt_start_time': '23:30',
                    },
                    {
                        'speaker': '追加登壇者',
                        'theme': '追加テーマ',
                        'lt_start_time': '23:50',
                    },
                ],
                initial_forms=1,
            )
        )

        response = self.client.post(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk}),
            data=post_data,
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        participation.refresh_from_db()
        presentation.refresh_from_db()
        self.assertEqual(participation.requested_date, self.collaboration.period_start)
        self.assertEqual(participation.requested_start_time, time(21, 0))
        self.assertEqual(participation.requested_duration, 60)
        self.assertEqual(participation.organizer_note, '確定後も備考は更新')
        self.assertEqual(participation.lt_slot_minutes, 20)
        self.assertEqual(presentation.speaker, '更新後登壇者')
        self.assertEqual(presentation.theme, '更新後テーマ')
        self.assertEqual(presentation.requested_start_time, time(23, 30))
        self.assertEqual(
            VketPresentation.objects.get(participation=participation, order=1).requested_start_time,
            time(23, 50),
        )

    def test_confirmed_participation_without_event_can_update_lt_and_note(self):
        """Eventがない確定済み参加でも発表と備考を更新できる"""
        Event.objects.filter(community=self.community).delete()
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            requested_date=self.collaboration.period_start,
            requested_start_time=time(21, 0),
            requested_duration=60,
            confirmed_date=self.collaboration.period_start,
            confirmed_start_time=time(21, 0),
            confirmed_duration=60,
            schedule_confirmed_at=timezone.now(),
            progress=VketParticipation.Progress.REHEARSAL,
            applied_by=self.owner,
        )
        presentation = VketPresentation.objects.create(
            participation=participation,
            order=0,
            speaker='更新前登壇者',
            theme='更新前テーマ',
            requested_start_time=time(21, 30),
            status=VketPresentation.Status.CONFIRMED,
        )

        self.client.force_login(self.owner)
        self._set_active_community()
        post_data = {
            'requested_date': self.collaboration.period_start.isoformat(),
            'requested_start_time': '23:00',
            'requested_duration': '90',
            'organizer_note': 'Eventなしでも更新できる備考',
        }
        post_data.update(
            self._make_formset_data(
                [{'speaker': '更新後登壇者', 'theme': '更新後テーマ'}],
                initial_forms=1,
            )
        )

        response = self.client.post(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk}),
            data=post_data,
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        participation.refresh_from_db()
        presentation.refresh_from_db()
        self.assertEqual(participation.requested_date, self.collaboration.period_start)
        self.assertEqual(participation.requested_start_time, time(21, 0))
        self.assertEqual(participation.requested_duration, 60)
        self.assertEqual(participation.confirmed_date, self.collaboration.period_start)
        self.assertEqual(participation.confirmed_start_time, time(21, 0))
        self.assertEqual(participation.confirmed_duration, 60)
        self.assertEqual(participation.organizer_note, 'Eventなしでも更新できる備考')
        self.assertEqual(presentation.speaker, '更新後登壇者')
        self.assertEqual(presentation.theme, '更新後テーマ')
        self.assertEqual(presentation.requested_start_time, time(21, 30))

    def test_confirmed_participation_post_does_not_delete_existing_lt(self):
        """日程確定後はformset DELETEでも既存LTを削除しない"""
        self.client.force_login(self.owner)
        self._set_active_community()

        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            requested_date=self.collaboration.period_start,
            requested_start_time=time(21, 0),
            requested_duration=60,
            confirmed_date=self.collaboration.period_start,
            confirmed_start_time=time(21, 0),
            confirmed_duration=60,
            schedule_confirmed_at=timezone.now(),
            progress=VketParticipation.Progress.REHEARSAL,
        )
        presentation = VketPresentation.objects.create(
            participation=participation,
            order=0,
            speaker='削除されない登壇者',
            theme='削除されないテーマ',
            requested_start_time=time(21, 30),
        )

        post_data = {
            'requested_date': self.collaboration.period_start.isoformat(),
            'requested_start_time': '21:00',
            'requested_duration': '60',
            'organizer_note': '',
        }
        post_data.update(
            self._make_formset_data(
                [
                    {
                        'speaker': '削除されない登壇者',
                        'theme': '削除されないテーマ',
                        'DELETE': True,
                    }
                ],
                initial_forms=1,
            )
        )

        response = self.client.post(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk}),
            data=post_data,
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(VketPresentation.objects.filter(pk=presentation.pk).exists())

    def test_confirmed_participation_apply_get_shows_lt_editable_message(self):
        """日程確定後も締切内のLT時刻編集可否を案内する"""
        self.client.force_login(self.owner)
        self._set_active_community()
        VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            requested_date=self.collaboration.period_start,
            requested_start_time=time(21, 0),
            requested_duration=60,
            confirmed_date=self.collaboration.period_start,
            confirmed_start_time=time(21, 0),
            confirmed_duration=60,
            schedule_confirmed_at=timezone.now(),
        )

        response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '参加日程（Step 1）は運営が確定済みのため編集できません。')
        self.assertContains(response, '発表開始時刻は発表情報の締切まで変更できます')

    def test_lt_start_time_is_rejected_after_deadline_but_text_updates(self):
        """締切後はLT時刻を保持し、登壇者名とテーマは更新できる"""
        self.client.force_login(self.owner)
        self._set_active_community()
        self.collaboration.lt_deadline = timezone.localdate() - timedelta(days=1)
        self.collaboration.save(update_fields=['lt_deadline'])
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration, community=self.community,
            requested_date=self.collaboration.period_start,
            requested_start_time=time(21, 0), requested_duration=60,
        )
        presentation = VketPresentation.objects.create(
            participation=participation, order=0, speaker='変更前登壇者',
            theme='変更前テーマ', requested_start_time=time(21, 30),
        )
        post_data = {
            'requested_date': self.collaboration.period_start.isoformat(),
            'requested_start_time': '21:00', 'requested_duration': '60',
            'organizer_note': '', 'lt_slot_minutes': '45',
        }
        post_data.update(self._make_formset_data([
            {'speaker': '変更後登壇者', 'theme': '変更後テーマ', 'lt_start_time': '23:30'},
        ], initial_forms=1))

        response = self.client.post(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk}), post_data
        )

        self.assertEqual(response.status_code, 302)
        presentation.refresh_from_db()
        self.assertEqual(presentation.speaker, '変更後登壇者')
        self.assertEqual(presentation.theme, '変更後テーマ')
        self.assertEqual(presentation.requested_start_time, time(21, 30))

    def test_confirmed_or_published_lt_time_ignores_post_value(self):
        """確定済みまたは公開済みLTの時刻はPOST値で上書きできない"""
        self.client.force_login(self.owner)
        self._set_active_community()
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration, community=self.community,
            requested_date=self.collaboration.period_start,
            requested_start_time=time(21, 0), requested_duration=60,
        )
        confirmed = VketPresentation.objects.create(
            participation=participation, order=0, speaker='確定済み', theme='テーマ',
            requested_start_time=time(21, 30), status=VketPresentation.Status.CONFIRMED,
        )
        published_detail = EventDetail.objects.create(
            event=Event.objects.get(community=self.community),
            speaker='公開済み', theme='テーマ', start_time=time(22, 0),
        )
        published = VketPresentation.objects.create(
            participation=participation, order=1, speaker='公開済み', theme='テーマ',
            requested_start_time=time(22, 0), published_event_detail=published_detail,
        )
        post_data = {
            'requested_date': self.collaboration.period_start.isoformat(),
            'requested_start_time': '21:00', 'requested_duration': '60',
            'organizer_note': '', 'lt_slot_minutes': '30',
        }
        post_data.update(self._make_formset_data([
            {'speaker': '確定済み更新', 'theme': '更新', 'lt_start_time': '23:00'},
            {'speaker': '公開済み更新', 'theme': '更新', 'lt_start_time': '23:30'},
        ], initial_forms=2))

        response = self.client.post(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk}), post_data
        )

        self.assertEqual(response.status_code, 302)
        confirmed.refresh_from_db()
        published.refresh_from_db()
        self.assertEqual(confirmed.requested_start_time, time(21, 30))
        self.assertEqual(published.requested_start_time, time(22, 0))
