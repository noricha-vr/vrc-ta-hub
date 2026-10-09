"""Vket コラボの参加枠の重なり判定（#695）のテスト."""

from datetime import time, timedelta
from unittest import mock

from django.db.models import QuerySet
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from event.models import Event
from tests.factories import make_community, make_event, make_user
from vket.models import VketCollaboration, VketParticipation
from vket.schedule import (
    ScheduleBlock,
    block_for,
    find_conflicts,
    get_schedule_buffer_minutes,
    ranges_conflict,
)

from ._vket_test_bases import VketApplyFlowBase


class RangesConflictTests(TestCase):
    def test_adjacent_ranges_do_not_conflict_without_buffer(self):
        """終了と開始が接しているだけなら重ならない"""
        self.assertFalse(ranges_conflict(time(21, 0), 60, time(22, 0), 60))

    def test_buffer_makes_adjacent_ranges_conflict(self):
        """間隔を足すと、接している枠も重なりとみなす"""
        self.assertTrue(ranges_conflict(time(21, 0), 60, time(22, 0), 60, buffer_minutes=15))
        self.assertFalse(ranges_conflict(time(21, 0), 60, time(22, 15), 60, buffer_minutes=15))

    def test_buffer_setting_ignores_invalid_values(self):
        """settings_json の不正値は 0 分として扱う"""
        collaboration = VketCollaboration(settings_json={'schedule_buffer_minutes': 'abc'})
        self.assertEqual(get_schedule_buffer_minutes(collaboration), 0)
        collaboration.settings_json = None
        self.assertEqual(get_schedule_buffer_minutes(collaboration), 0)


class VketApplyScheduleOverlapTests(VketApplyFlowBase):
    """主催者の申込みで、他の集会と重なる時間を止める"""

    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.other_community = make_community(
            name='ゲーム開発集会', status='approved', frequency='毎週', weekdays=[], organizers='',
        )
        self.other = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.other_community,
            requested_date=self.today,
            requested_start_time='21:00',
            requested_duration=60,
            progress=VketParticipation.Progress.APPLIED,
        )
        self.client.force_login(self.owner)
        self._set_active_community()

    def _post_apply(self, start: str, duration: str = '60', client=None):
        data = {
            'requested_date': self.today.isoformat(),
            'requested_start_time': start,
            'requested_duration': duration,
            'organizer_note': '',
        }
        data.update(self._make_formset_data([]))
        return (client or self.client).post(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk}), data=data,
        )

    def _own_participation(self):
        return VketParticipation.objects.filter(
            collaboration=self.collaboration, community=self.community,
        ).first()

    def test_organizer_cannot_save_overlapping_time(self):
        """他の集会と重なる時間では保存できず、集会名つきで知らせる"""
        response = self._post_apply('21:30')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'その時間はゲーム開発集会が申込み済みです。')
        self.assertIsNone(self._own_participation())

    def test_organizer_can_move_to_free_time(self):
        """空いている時間には申し込め、後から別の空き時間へ動かせる"""
        response = self._post_apply('22:00')
        self.assertEqual(response.status_code, 302)

        response = self._post_apply('23:00')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._own_participation().requested_start_time, time(23, 0))

    def test_own_slot_does_not_block_itself(self):
        """自分の枠と重なる時間へ動かしても、自分自身では弾かれない"""
        self.assertEqual(self._post_apply('22:00').status_code, 302)

        response = self._post_apply('22:30')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._own_participation().requested_start_time, time(22, 30))

    def test_buffer_is_included_in_judgement(self):
        """入れ替えの間隔を前後に足して判定する"""
        self.collaboration.settings_json = {'schedule_buffer_minutes': 15}
        self.collaboration.save(update_fields=['settings_json'])

        response = self._post_apply('22:00')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '前後 15 分は入れ替えの時間として空けてください。')

        self.assertEqual(self._post_apply('22:15').status_code, 302)

    def test_confirmed_schedule_takes_priority_over_requested(self):
        """相手に確定値があれば、希望値でなく確定値で判定する"""
        self.other.confirmed_date = self.today
        self.other.confirmed_start_time = time(23, 0)
        self.other.confirmed_duration = 60
        self.other.save()

        self.assertEqual(self._post_apply('21:00').status_code, 302)
        own = self._own_participation()
        own.delete()
        response = self._post_apply('23:30')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ゲーム開発集会が申込み済みです')

    def test_withdrawn_participation_is_not_counted(self):
        """辞退・不参加の参加は重なりの相手に含めない"""
        self.other.lifecycle = VketParticipation.Lifecycle.WITHDRAWN
        self.other.save(update_fields=['lifecycle'])

        self.assertEqual(self._post_apply('21:00').status_code, 302)

    def test_second_concurrent_application_is_rejected_under_lock(self):
        """同時の申込みは、コラボの行をロックした中で判定し、2 件目を弾く"""
        third_user = make_user(user_name='third_owner', email='third@example.com')
        third = make_community(
            name='VR集会', owner=third_user, status='approved', frequency='毎週', weekdays=[], organizers='',
        )
        make_event(
            third, event_date=self.today, start_time='22:00', duration=60, weekday='',
            accepts_lt_application=True,
        )

        # テスト DB はロックを持たないため、select_for_update の呼び出しで判定する
        original = QuerySet.select_for_update
        with mock.patch.object(QuerySet, 'select_for_update', autospec=True) as spy:
            spy.side_effect = lambda qs, *args, **kwargs: original(qs, *args, **kwargs)
            first = self._post_apply('22:00')
        self.assertEqual(first.status_code, 302)
        self.assertTrue(
            any(call.args[0].model is VketCollaboration for call in spy.call_args_list),
            'コラボの行を select_for_update でロックしていない',
        )

        self.client.force_login(third_user)
        session = self.client.session
        session['active_community_id'] = third.id
        session.save()
        second = self._post_apply('22:30')
        self.assertEqual(second.status_code, 200)
        self.assertContains(second, 'その時間は個人開発集会が申込み済みです。')
        self.assertFalse(
            VketParticipation.objects.filter(collaboration=self.collaboration, community=third).exists()
        )

    def test_lt_only_update_is_not_blocked_by_existing_overlap(self):
        """希望の時間を変えない保存（発表情報だけの更新）は、既存の重なりで止めない"""
        VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            requested_date=self.today,
            requested_start_time='21:00',
            requested_duration=60,
            progress=VketParticipation.Progress.APPLIED,
        )

        self.assertEqual(self._post_apply('21:00').status_code, 302)

    def test_apply_page_shows_busy_blocks_of_other_communities(self):
        """申込みフォームに、他の集会の埋まっている時間帯（集会名と時間だけ）を出す"""
        response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))

        busy = response.context['busy_blocks_by_date']
        self.assertEqual(
            busy[self.today.isoformat()],
            [{
                'start': '21:00', 'end': '22:00', 'start_minutes': 1260,
                'end_minutes': 1320, 'name': 'ゲーム開発集会',
            }],
        )
        self.assertContains(response, 'id="busy-blocks-data"')
        self.assertContains(response, 'id="busy-slots"')


class VketAdminScheduleOverlapTests(TestCase):
    """運営は重なっても保存でき、確定に当たる操作だけ止める"""

    def setUp(self):
        self.admin = make_user(
            user_name='vket_admin', email='vket_admin@example.com', is_staff=True, is_superuser=True,
        )
        self.today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            slug='vket-overlap-admin',
            name='重なり確認コラボ',
            period_start=self.today,
            period_end=self.today + timedelta(days=7),
            registration_deadline=self.today + timedelta(days=1),
            lt_deadline=self.today + timedelta(days=3),
            phase=VketCollaboration.Phase.SCHEDULING,
            settings_json={'stage_url': 'https://example.com/stage'},
        )
        self.a = self._participation('集会A', time(21, 0))
        self.b = self._participation('集会B', time(22, 0))
        self.client.force_login(self.admin)

    def _participation(self, name: str, start: time) -> VketParticipation:
        community = make_community(name=name, status='approved', frequency='毎週', weekdays=[], organizers='')
        return VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=community,
            confirmed_date=self.today,
            confirmed_start_time=start,
            confirmed_duration=60,
            schedule_confirmed_at=timezone.now(),
            progress=VketParticipation.Progress.REHEARSAL,
        )

    def _update(self, participation: VketParticipation, start: str):
        return self.client.post(
            reverse(
                'vket:manage_participation_update',
                kwargs={'pk': self.collaboration.pk, 'participation_id': participation.pk},
            ),
            data={
                'lifecycle': VketParticipation.Lifecycle.ACTIVE,
                'confirmed_date': self.today.isoformat(),
                'confirmed_start_time': start,
                'confirmed_duration': '60',
                'admin_note': '入れ替え中',
            },
            follow=True,
        )

    def test_admin_can_save_overlapping_schedule_but_it_is_not_confirmed(self):
        """運営は重なる時間でも保存できるが、日程の確定（記録・公開同期）はしない"""
        new_one = self._participation('集会C', time(23, 0))
        new_one.schedule_confirmed_at = None
        new_one.progress = VketParticipation.Progress.APPLIED
        new_one.save()

        response = self._update(new_one, '22:30')

        new_one.refresh_from_db()
        self.assertEqual(new_one.confirmed_start_time, time(22, 30))
        self.assertEqual(new_one.admin_note, '入れ替え中')
        self.assertIsNone(new_one.schedule_confirmed_at)
        self.assertEqual(new_one.progress, VketParticipation.Progress.APPLIED)
        self.assertIsNone(new_one.published_event_id)
        self.assertContains(response, '確定していません')
        self.assertContains(response, '集会B')

    def test_admin_can_swap_two_slots(self):
        """入れ替えの途中は重なっても保存でき、解消後の確定で確定される"""
        self._update(self.a, '22:00')
        self._update(self.b, '21:00')
        response = self._update(self.a, '22:00')

        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual(self.a.confirmed_start_time, time(22, 0))
        self.assertEqual(self.b.confirmed_start_time, time(21, 0))
        self.assertContains(response, '集会A の日程を確定しました。')

    def test_schedule_page_warns_overlap_with_buffer(self):
        """日程画面の警告も共通の判定（間隔込み）を使う"""
        url = reverse('vket:manage_schedule', kwargs={'pk': self.collaboration.pk})
        self.assertEqual(self.client.get(url).context['overlap_warnings'], [])

        self.collaboration.settings_json = {'schedule_buffer_minutes': 10}
        self.collaboration.save(update_fields=['settings_json'])

        warnings = self.client.get(url).context['overlap_warnings']
        self.assertEqual(len(warnings), 1)
        self.assertIn('集会A', warnings[0])
        self.assertIn('集会B', warnings[0])

    def test_publish_is_blocked_when_overlap_remains(self):
        """公開同期は重なりが残っていると実行せず、重なっている組を表示する"""
        self.b.confirmed_start_time = time(21, 30)
        self.b.save(update_fields=['confirmed_start_time'])
        self.collaboration.phase = VketCollaboration.Phase.LOCKED
        self.collaboration.save(update_fields=['phase'])

        response = self.client.post(
            reverse('vket:manage_publish', kwargs={'pk': self.collaboration.pk}), follow=True,
        )

        self.assertContains(response, '公開同期を実行しませんでした')
        self.assertContains(response, '集会A（21:00〜22:00）と 集会B（21:30〜22:30）')
        self.assertFalse(Event.objects.filter(community__in=[self.a.community, self.b.community]).exists())
        self.a.refresh_from_db()
        self.assertNotEqual(self.a.progress, VketParticipation.Progress.DONE)

    def test_publish_runs_when_no_overlap(self):
        """重なりが無ければ公開同期を実行する"""
        self.collaboration.phase = VketCollaboration.Phase.LOCKED
        self.collaboration.save(update_fields=['phase'])

        response = self.client.post(
            reverse('vket:manage_publish', kwargs={'pk': self.collaboration.pk}), follow=True,
        )

        self.assertContains(response, '2件のイベントを公開しました')

    def test_buffer_setting_is_saved_keeping_other_keys(self):
        """入れ替えの間隔を保存しても settings_json の他のキーは残る"""
        response = self.client.post(
            reverse('vket:manage_schedule_settings', kwargs={'pk': self.collaboration.pk}),
            data={'schedule_buffer_minutes': '15'},
        )

        self.assertEqual(response.status_code, 302)
        self.collaboration.refresh_from_db()
        self.assertEqual(
            self.collaboration.settings_json,
            {'stage_url': 'https://example.com/stage', 'schedule_buffer_minutes': 15},
        )

    def test_buffer_setting_rejects_negative_value(self):
        """負の値は保存しない"""
        self.client.post(
            reverse('vket:manage_schedule_settings', kwargs={'pk': self.collaboration.pk}),
            data={'schedule_buffer_minutes': '-5'},
        )
        self.collaboration.refresh_from_db()
        self.assertNotIn('schedule_buffer_minutes', self.collaboration.settings_json)

    def test_buffer_setting_requires_admin(self):
        """運営以外は入れ替えの間隔を変えられない"""
        user = make_user(user_name='plain_user', email='plain@example.com')
        self.client.force_login(user)
        response = self.client.post(
            reverse('vket:manage_schedule_settings', kwargs={'pk': self.collaboration.pk}),
            data={'schedule_buffer_minutes': '15'},
        )
        self.assertEqual(response.status_code, 403)

    def test_find_conflicts_uses_confirmed_and_skips_declined(self):
        """共通の判定関数は確定値を優先し、不参加の参加を相手に含めない"""
        candidate = ScheduleBlock(
            participation_id=None, community_id=-1, community_name='候補',
            date=self.today, start=time(21, 30), duration=30,
        )
        self.assertEqual([b.community_name for b in find_conflicts(self.collaboration, candidate)], ['集会A'])

        self.a.lifecycle = VketParticipation.Lifecycle.DECLINED
        self.a.save(update_fields=['lifecycle'])
        self.assertEqual(find_conflicts(self.collaboration, candidate), [])
        self.assertTrue(block_for(self.b).is_confirmed)
