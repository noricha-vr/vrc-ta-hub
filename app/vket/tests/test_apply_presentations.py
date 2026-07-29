"""Vketコラボ機能のテスト."""

from datetime import time

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
    def test_lt_start_time_saved_to_presentation(self):
        """LT開始時刻が VketPresentation.requested_start_time に保存される"""
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
                'lt-TOTAL_FORMS': '1',
                'lt-INITIAL_FORMS': '0',
                'lt-MIN_NUM_FORMS': '0',
                'lt-MAX_NUM_FORMS': '20',
                'lt-0-speaker': 'テスト登壇者',
                'lt-0-theme': 'テストテーマ',
                'lt-0-lt_start_time': '21:30',
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        participation = VketParticipation.objects.get(
            collaboration=self.collaboration, community=self.community
        )
        pres = VketPresentation.objects.get(participation=participation, order=0)
        self.assertEqual(pres.requested_start_time.strftime('%H:%M'), '21:30')

    def test_apply_creates_multiple_presentations(self):
        """複数LTを送信するとDBに複数件作成される"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()

        target_date = self.collaboration.period_start
        lt_rows = [
            {'speaker': '登壇者A', 'theme': 'テーマA'},
            {'speaker': '登壇者B', 'theme': 'テーマB'},
            {'speaker': '登壇者C', 'theme': 'テーマC'},
        ]
        post_data = {
            'requested_date': target_date.isoformat(),
            'requested_start_time': '21:00',
            'requested_duration': '60',
            'organizer_note': '',
        }
        post_data.update(self._make_formset_data(lt_rows))

        response = self.client.post(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk}),
            data=post_data,
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        participation = VketParticipation.objects.get(
            collaboration=self.collaboration, community=self.community
        )
        presentations = list(
            VketPresentation.objects.filter(participation=participation).order_by('order')
        )
        self.assertEqual(len(presentations), 3)
        self.assertEqual(presentations[0].speaker, '登壇者A')
        self.assertEqual(presentations[0].order, 0)
        self.assertEqual(presentations[1].speaker, '登壇者B')
        self.assertEqual(presentations[1].order, 1)
        self.assertEqual(presentations[2].speaker, '登壇者C')
        self.assertEqual(presentations[2].order, 2)

    def test_apply_deletes_presentation(self):
        """DELETEフラグ付きで送信すると該当レコードが削除される"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()

        # 先に参加と2件のプレゼンを作成
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            requested_date=self.collaboration.period_start,
            requested_start_time='21:00',
            requested_duration=60,
            progress=VketParticipation.Progress.APPLIED,
            applied_by=self.owner,
        )
        VketPresentation.objects.create(
            participation=participation, order=0, speaker='残す登壇者', theme='残すテーマ'
        )
        VketPresentation.objects.create(
            participation=participation, order=1, speaker='消す登壇者', theme='消すテーマ'
        )

        target_date = self.collaboration.period_start
        lt_rows = [
            {'speaker': '残す登壇者', 'theme': '残すテーマ'},
            {'speaker': '消す登壇者', 'theme': '消すテーマ', 'DELETE': True},
        ]
        post_data = {
            'requested_date': target_date.isoformat(),
            'requested_start_time': '21:00',
            'requested_duration': '60',
            'organizer_note': '',
        }
        post_data.update(self._make_formset_data(lt_rows, initial_forms=2))

        response = self.client.post(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk}),
            data=post_data,
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        presentations = list(
            VketPresentation.objects.filter(participation=participation).order_by('order')
        )
        self.assertEqual(len(presentations), 1)
        self.assertEqual(presentations[0].speaker, '残す登壇者')


    def test_missing_lt_start_times_are_assigned_from_slot_minutes(self):
        """未入力LT時刻は参加枠の開始時刻から持ち時間ごとに補完する"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()
        post_data = {
            'requested_date': self.collaboration.period_start.isoformat(),
            'requested_start_time': '21:00', 'requested_duration': '60',
            'organizer_note': '', 'lt_slot_minutes': '20',
        }
        post_data.update(self._make_formset_data([
            {'speaker': '登壇者1', 'theme': 'テーマ1'},
            {'speaker': '登壇者2', 'theme': 'テーマ2'},
            {'speaker': '登壇者3', 'theme': 'テーマ3'},
        ]))

        response = self.client.post(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk}), post_data
        )

        self.assertEqual(response.status_code, 302)
        participation = VketParticipation.objects.get(
            collaboration=self.collaboration, community=self.community
        )
        self.assertEqual(participation.lt_slot_minutes, 20)
        self.assertEqual(
            list(participation.presentations.values_list('requested_start_time', flat=True)),
            [time(21, 0), time(21, 20), time(21, 40)],
        )

    def test_lt_slot_minutes_defaults_to_30(self):
        """参加枠の持ち時間は30分を既定値にする"""
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration, community=self.community,
        )
        self.assertEqual(participation.lt_slot_minutes, 30)

    def test_apply_get_prefills_multiple_presentations(self):
        """既存の複数LTがGETでformsetにプリフィルされる"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()

        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            requested_date=self.collaboration.period_start,
            requested_start_time='21:00',
            requested_duration=60,
            progress=VketParticipation.Progress.APPLIED,
            applied_by=self.owner,
        )
        VketPresentation.objects.create(
            participation=participation, order=0, speaker='登壇者1', theme='テーマ1'
        )
        VketPresentation.objects.create(
            participation=participation, order=1, speaker='登壇者2', theme='テーマ2'
        )
        VketPresentation.objects.create(
            participation=participation, order=2, speaker='登壇者3', theme='テーマ3'
        )

        response = self.client.get(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk})
        )
        self.assertEqual(response.status_code, 200)

        formset = response.context['formset']
        # initial + extra(1) = 4 forms
        self.assertEqual(len(formset.forms), 4)
        self.assertEqual(formset.forms[0].initial['speaker'], '登壇者1')
        self.assertEqual(formset.forms[1].initial['speaker'], '登壇者2')
        self.assertEqual(formset.forms[2].initial['speaker'], '登壇者3')

    def test_apply_skips_empty_presentation_rows(self):
        """空行はスキップされる（DBに保存されない）"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()

        target_date = self.collaboration.period_start
        lt_rows = [
            {'speaker': '登壇者A', 'theme': 'テーマA'},
            {'speaker': '', 'theme': ''},
            {'speaker': '登壇者C', 'theme': 'テーマC'},
        ]
        post_data = {
            'requested_date': target_date.isoformat(),
            'requested_start_time': '21:00',
            'requested_duration': '60',
            'organizer_note': '',
        }
        post_data.update(self._make_formset_data(lt_rows))

        response = self.client.post(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk}),
            data=post_data,
            follow=False,
        )
        self.assertEqual(response.status_code, 302)

        participation = VketParticipation.objects.get(
            collaboration=self.collaboration, community=self.community
        )
        presentations = list(
            VketPresentation.objects.filter(participation=participation).order_by('order')
        )
        self.assertEqual(len(presentations), 2)
        self.assertEqual(presentations[0].speaker, '登壇者A')
        self.assertEqual(presentations[0].order, 0)
        self.assertEqual(presentations[1].speaker, '登壇者C')
        self.assertEqual(presentations[1].order, 1)

    def test_presentation_delete_by_organizer(self):
        """主催者がLTを削除できる"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            progress=VketParticipation.Progress.APPLIED,
        )
        pres = VketPresentation.objects.create(
            participation=participation,
            order=0,
            speaker='削除テスト',
            theme='テーマ',
            status=VketPresentation.Status.DRAFT,
        )
        response = self.client.post(
            reverse(
                'vket:presentation_delete',
                kwargs={'pk': self.collaboration.pk, 'presentation_id': pres.pk},
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(VketPresentation.objects.filter(pk=pres.pk).exists())

    def test_confirmed_presentation_delete_forbidden_for_organizer(self):
        """確定済みLTは主催者側の個別削除を拒否する"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            confirmed_date=self.collaboration.period_start,
            confirmed_start_time=time(21, 0),
            confirmed_duration=60,
            schedule_confirmed_at=timezone.now(),
            progress=VketParticipation.Progress.REHEARSAL,
        )
        pres = VketPresentation.objects.create(
            participation=participation,
            order=0,
            speaker='確定済みLT',
            theme='テーマ',
            status=VketPresentation.Status.CONFIRMED,
        )

        response = self.client.post(
            reverse(
                'vket:presentation_delete',
                kwargs={'pk': self.collaboration.pk, 'presentation_id': pres.pk},
            ),
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(VketPresentation.objects.filter(pk=pres.pk).exists())

    def test_published_presentation_delete_forbidden_for_organizer(self):
        """公開済みLTは主催者側の個別削除を拒否する"""
        self.client.login(username='owner_user', password='testpass123')
        self._set_active_community()
        event = Event.objects.filter(community=self.community).first()
        detail = EventDetail.objects.create(
            event=event,
            detail_type='LT',
            start_time='21:30',
            duration=30,
            status='approved',
        )
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            progress=VketParticipation.Progress.APPLIED,
        )
        pres = VketPresentation.objects.create(
            participation=participation,
            order=0,
            speaker='公開済みLT',
            theme='テーマ',
            published_event_detail=detail,
        )

        response = self.client.post(
            reverse(
                'vket:presentation_delete',
                kwargs={'pk': self.collaboration.pk, 'presentation_id': pres.pk},
            ),
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(VketPresentation.objects.filter(pk=pres.pk).exists())
        self.assertTrue(EventDetail.objects.filter(pk=detail.pk).exists())

    def test_presentation_delete_forbidden_for_non_member(self):
        """コミュニティに所属しないユーザーはLTを削除できない"""
        User.objects.create_user(
            user_name='non_member_user',
            email='nonmember@example.com',
            password='testpass123',
        )
        self.client.login(username='non_member_user', password='testpass123')
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            progress=VketParticipation.Progress.APPLIED,
        )
        pres = VketPresentation.objects.create(
            participation=participation,
            order=0,
            speaker='テスト',
            status=VketPresentation.Status.DRAFT,
        )
        response = self.client.post(
            reverse(
                'vket:presentation_delete',
                kwargs={'pk': self.collaboration.pk, 'presentation_id': pres.pk},
            ),
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(VketPresentation.objects.filter(pk=pres.pk).exists())
