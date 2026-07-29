"""Vketコラボ機能のテスト."""

from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from event.models import Event
from vket.models import (
    VketCollaboration,
    VketParticipation,
    VketPresentation,
)


User = get_user_model()


from ._vket_test_bases import VketApplyFlowBase


class VketApplyFlowTests(VketApplyFlowBase):
    def test_apply_get_allows_staff(self):
        """スタッフもapplyページにアクセスできる"""
        self.client.login(username='other_user', password='testpass123')
        self._set_active_community()
        response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 200)

    def test_apply_get_shows_organizer_note_guidance_above_textarea(self):
        """備考欄の案内文がテキストエリア外に表示される"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()

        response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'スタッフが少なく、当日ステージへの案内などサポートが欲しい場合は「サポート希望」と記載してください。',
        )
        self.assertContains(
            response,
            'その他、運営に連絡事項があれば記載をお願いします。',
        )
        self.assertContains(response, 'background: #eef6ff; color: #2f5f8f;')
        self.assertContains(response, 'text-decoration-underline')

    def test_apply_post_creates_participation_and_presentation(self):
        """主催者が申請するとVketParticipationとVketPresentationが作成される"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()

        target_date = self.collaboration.period_start
        response = self.client.post(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk}),
            data={
                'requested_date': target_date.isoformat(),
                'requested_start_time': '21:00',
                'requested_duration': '60',
                'organizer_note': '備考テスト',
                # formset management form
                'lt-TOTAL_FORMS': '1',
                'lt-INITIAL_FORMS': '0',
                'lt-MIN_NUM_FORMS': '0',
                'lt-MAX_NUM_FORMS': '20',
                # LT data
                'lt-0-speaker': 'テスト登壇者',
                'lt-0-theme': 'テストテーマ',
                'lt-0-lt_start_time': '',
            },
            follow=False,
        )

        self.assertEqual(response.status_code, 302)

        participation = VketParticipation.objects.get(
            collaboration=self.collaboration, community=self.community
        )
        # 希望日程が保存されている
        self.assertEqual(participation.requested_date, target_date)
        self.assertEqual(participation.requested_start_time.strftime('%H:%M'), '21:00')
        self.assertEqual(participation.requested_duration, 60)
        self.assertEqual(participation.organizer_note, '備考テスト')
        # applied_byがセットされている
        self.assertEqual(participation.applied_by, self.owner)
        self.assertIsNotNone(participation.applied_at)
        self.assertEqual(participation.progress, VketParticipation.Progress.APPLIED)

        # 確定前なのでEventは作成されない
        self.assertIsNone(participation.published_event_id)

        # VketPresentationが作成されている
        pres = VketPresentation.objects.get(participation=participation, order=0)
        self.assertEqual(pres.speaker, 'テスト登壇者')
        self.assertEqual(pres.theme, 'テストテーマ')

    def test_apply_is_forbidden_after_registration_deadline_for_new_participation(self):
        """参加申請締切後は新規参加登録が403になる"""
        self.collaboration.registration_deadline = timezone.localdate() - timedelta(days=1)
        self.collaboration.save()

        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()
        response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 403)

    def test_detail_can_apply_is_false_when_registration_closed_and_no_participation(self):
        """締切後・未参加の場合 can_apply=False"""
        self.collaboration.registration_deadline = timezone.localdate() - timedelta(days=1)
        self.collaboration.save()

        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()
        response = self.client.get(reverse('vket:detail', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_apply'])

    def test_detail_can_apply_is_true_when_lt_open_and_participation_has_published_event(self):
        """LT締切内かつpublished_eventがある場合 can_apply=True"""
        today = timezone.localdate()
        self.collaboration.registration_deadline = today - timedelta(days=1)
        self.collaboration.lt_deadline = today + timedelta(days=1)
        self.collaboration.phase = VketCollaboration.Phase.SCHEDULING
        self.collaboration.save()

        weekday_code = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][today.weekday()]
        event = Event.objects.create(
            community=self.community,
            date=today,
            start_time='21:00',
            duration=60,
            weekday=weekday_code,
        )
        VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            published_event=event,
        )

        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()
        response = self.client.get(reverse('vket:detail', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_apply'])

    def test_apply_accepts_late_lt_submission_as_draft_for_existing_participation(self):
        """発表締切後も既存参加団体の発表情報は申請中で保存できる"""
        today = timezone.localdate()
        self.collaboration.registration_deadline = today - timedelta(days=3)
        self.collaboration.lt_deadline = today - timedelta(days=1)
        self.collaboration.phase = VketCollaboration.Phase.ANNOUNCEMENT
        self.collaboration.save()
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            requested_date=self.collaboration.period_start,
            requested_start_time=time(21, 0),
            requested_duration=60,
            progress=VketParticipation.Progress.APPLIED,
            applied_by=self.owner,
        )

        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()

        response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['permissions'].can_edit_lt)
        self.assertTrue(response.context['is_late_lt_submission'])
        self.assertContains(response, '運営の確認後に確定・公開されます。')

        post_data = {
            'requested_date': self.collaboration.period_start.isoformat(),
            'requested_start_time': '21:00',
            'requested_duration': '60',
            'organizer_note': '締切後の追記',
        }
        post_data.update(
            self._make_formset_data([
                {'speaker': '締切後登壇者', 'theme': '締切後テーマ'}
            ])
        )

        response = self.client.post(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk}),
            data=post_data,
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        participation.refresh_from_db()
        presentation = VketPresentation.objects.get(participation=participation)
        self.assertEqual(participation.organizer_note, '締切後の追記')
        self.assertEqual(presentation.speaker, '締切後登壇者')
        self.assertEqual(presentation.status, VketPresentation.Status.DRAFT)

    def test_late_lt_update_resets_confirmed_presentation_to_draft(self):
        """締切後に確定済み発表を更新した場合は申請中に戻す"""
        today = timezone.localdate()
        self.collaboration.registration_deadline = today - timedelta(days=3)
        self.collaboration.lt_deadline = today - timedelta(days=1)
        self.collaboration.phase = VketCollaboration.Phase.ANNOUNCEMENT
        self.collaboration.save()
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
            speaker='確定済み登壇者',
            theme='確定済みテーマ',
            requested_start_time=time(21, 30),
            status=VketPresentation.Status.CONFIRMED,
        )

        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()

        post_data = {
            'requested_date': self.collaboration.period_start.isoformat(),
            'requested_start_time': '23:00',
            'requested_duration': '90',
            'organizer_note': '締切後更新',
        }
        post_data.update(
            self._make_formset_data(
                [
                    {
                        'speaker': '更新後登壇者',
                        'theme': '更新後テーマ',
                        'lt_start_time': '23:30',
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
        presentation.refresh_from_db()
        self.assertEqual(presentation.speaker, '更新後登壇者')
        self.assertEqual(presentation.theme, '更新後テーマ')
        self.assertEqual(presentation.requested_start_time.strftime('%H:%M'), '21:30')
        self.assertEqual(presentation.status, VketPresentation.Status.DRAFT)

    def test_apply_blocks_lt_submission_after_event_period(self):
        """開催期間後は発表情報も編集不可にする"""
        today = timezone.localdate()
        self.collaboration.period_start = today - timedelta(days=8)
        self.collaboration.period_end = today - timedelta(days=1)
        self.collaboration.registration_deadline = today - timedelta(days=7)
        self.collaboration.lt_deadline = today - timedelta(days=2)
        self.collaboration.phase = VketCollaboration.Phase.ANNOUNCEMENT
        self.collaboration.save()
        VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            requested_date=self.collaboration.period_start,
            requested_start_time=time(21, 0),
            requested_duration=60,
            progress=VketParticipation.Progress.APPLIED,
            applied_by=self.owner,
        )

        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()

        response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['permissions'].can_edit_lt)
        self.assertFalse(response.context['is_late_lt_submission'])
        self.assertContains(response, '発表情報（Step 2）は受付期間外のため編集できません。')
