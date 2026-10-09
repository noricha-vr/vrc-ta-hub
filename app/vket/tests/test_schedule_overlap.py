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
    blocks_conflict,
    busy_payload,
    find_conflicts,
    get_schedule_buffer_minutes,
    ranges_conflict,
    set_schedule_buffer_minutes,
)

from ._vket_test_bases import VketApplyFlowBase


def _block(name: str, day, start: time, duration: int, community_id: int = 0) -> ScheduleBlock:
    return ScheduleBlock(
        participation_id=None, community_id=community_id, community_name=name,
        date=day, start=start, duration=duration,
    )


class CrossMidnightTests(TestCase):
    """日付をまたぐ枠の判定と空き表示"""

    def setUp(self):
        self.day = timezone.localdate()
        self.next_day = self.day + timedelta(days=1)

    def test_block_crossing_midnight_conflicts_with_next_day_block(self):
        """23:30 から 90 分の枠は、翌日 00:30 からの枠と重なる"""
        late = _block('深夜集会', self.day, time(23, 30), 90, 1)
        early = _block('朝集会', self.next_day, time(0, 30), 60, 2)
        self.assertTrue(blocks_conflict(late, early))
        self.assertFalse(blocks_conflict(late, _block('朝集会', self.next_day, time(1, 0), 60, 2)))

    def test_buffer_applies_across_midnight(self):
        """前日 23:50 に終わる枠と翌日 00:00 からの枠は、間隔 15 分なら重なる"""
        late = _block('深夜集会', self.day, time(22, 50), 60, 1)
        early = _block('朝集会', self.next_day, time(0, 0), 60, 2)
        self.assertFalse(blocks_conflict(late, early))
        self.assertTrue(blocks_conflict(late, early, buffer_minutes=15))

    def test_busy_payload_shows_block_continuing_from_previous_day(self):
        """空き表示は、前日から続く枠も翌日の欄に「（前日から）」として出す"""
        payload = busy_payload([_block('深夜集会', self.day, time(23, 30), 90, 1)])

        self.assertEqual(
            payload['days'][self.day.isoformat()], [{'index': 0, 'label': '23:30〜翌01:00'}],
        )
        self.assertEqual(
            payload['days'][self.next_day.isoformat()],
            [{'index': 0, 'label': '〜01:00（前日から）'}],
        )
        block = payload['blocks'][0]
        self.assertEqual(block['name'], '深夜集会')
        self.assertEqual(block['end_abs'] - block['start_abs'], 90)

    def test_block_ending_at_midnight_does_not_touch_next_day(self):
        """ちょうど 0:00 に終わる枠は翌日の欄に出さない"""
        payload = busy_payload([_block('夜集会', self.day, time(23, 0), 60, 1)])
        self.assertNotIn(self.next_day.isoformat(), payload['days'])


class BufferSettingTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            slug='vket-buffer-setting', name='設定確認', period_start=today,
            period_end=today + timedelta(days=7), registration_deadline=today,
            lt_deadline=today, settings_json={'stage_url': 'https://example.com/stage'},
        )

    def test_non_dict_settings_read_as_zero(self):
        """settings_json が dict でない時は既定値 0 を返す"""
        for value in (['schedule_buffer_minutes', 15], 'schedule_buffer_minutes'):
            self.collaboration.settings_json = value
            self.assertEqual(get_schedule_buffer_minutes(self.collaboration), 0)

    def test_non_dict_settings_are_rebuilt_on_write(self):
        """settings_json が dict でない時は dict として作り直して保存する"""
        VketCollaboration.objects.filter(pk=self.collaboration.pk).update(settings_json=['broken'])

        set_schedule_buffer_minutes(self.collaboration, 10)

        self.collaboration.refresh_from_db()
        self.assertEqual(self.collaboration.settings_json, {'schedule_buffer_minutes': 10})

    def test_write_keeps_keys_updated_by_others_meanwhile(self):
        """読んだ後に他のキーが更新されていても、その更新を消さない"""
        stale = VketCollaboration.objects.get(pk=self.collaboration.pk)
        VketCollaboration.objects.filter(pk=self.collaboration.pk).update(
            settings_json={'stage_url': 'https://example.com/stage', 'notice_og_image': 'og.png'},
        )

        set_schedule_buffer_minutes(stale, 5)

        self.collaboration.refresh_from_db()
        self.assertEqual(
            self.collaboration.settings_json,
            {
                'stage_url': 'https://example.com/stage',
                'notice_og_image': 'og.png',
                'schedule_buffer_minutes': 5,
            },
        )
        self.assertEqual(stale.settings_json['schedule_buffer_minutes'], 5)


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

    def _post_apply(self, start: str, duration: str = '60', client=None, on_date=None):
        data = {
            'requested_date': (on_date or self.today).isoformat(),
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

    def test_organizer_is_blocked_by_block_crossing_midnight(self):
        """前日 23:30 から 90 分の枠と、翌日 00:30 からの申込みは重なりとして止める"""
        tomorrow = self.today + timedelta(days=1)
        make_event(
            self.community, event_date=tomorrow, start_time='00:30', duration=60, weekday='',
            accepts_lt_application=True,
        )
        self.other.requested_start_time = time(23, 30)
        self.other.requested_duration = 90
        self.other.save()

        response = self._post_apply('00:30', on_date=tomorrow)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'その時間はゲーム開発集会が申込み済みです。')
        self.assertEqual(self._post_apply('01:00', on_date=tomorrow).status_code, 302)

    def test_apply_page_shows_busy_blocks_of_other_communities(self):
        """申込みフォームに、他の集会の埋まっている時間帯（集会名と時間だけ）を出す"""
        response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))

        busy = response.context['busy_payload']
        self.assertEqual(busy['days'][self.today.isoformat()], [{'index': 0, 'label': '21:00〜22:00'}])
        self.assertEqual(busy['blocks'][0]['name'], 'ゲーム開発集会')
        self.assertEqual(set(busy['blocks'][0]), {'name', 'start_abs', 'end_abs'})
        self.assertContains(response, 'id="busy-blocks-data"')
        self.assertContains(response, 'id="busy-slots"')

    def test_busy_payload_reuses_schedule_participations(self):
        """空き表示は日程表で読んだ参加から作り、参加を読み直さない"""
        with mock.patch('vket.schedule.active_blocks') as reread:
            response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))

        self.assertEqual(response.status_code, 200)
        reread.assert_not_called()
        self.assertIn(self.today.isoformat(), response.context['busy_payload']['days'])

    def test_busy_payload_is_not_built_when_schedule_is_locked(self):
        """日程を編集できない時は空き表示を作らない"""
        VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            requested_date=self.today,
            requested_start_time='22:00',
            requested_duration=60,
            confirmed_date=self.today,
            confirmed_start_time='22:00',
            confirmed_duration=60,
            progress=VketParticipation.Progress.APPLIED,
        )

        with mock.patch('vket.views.apply.busy_payload') as build:
            response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))

        self.assertEqual(response.status_code, 200)
        build.assert_not_called()
        self.assertIsNone(response.context['busy_payload'])
        self.assertNotContains(response, 'id="busy-slots"')


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

    def test_row_confirm_with_overlap_confirms_and_warns(self):
        """行の「確定」は重なっても止めずに確定し、重なっている組を警告で出す"""
        new_one = self._participation('集会C', time(23, 0))
        new_one.schedule_confirmed_at = None
        new_one.progress = VketParticipation.Progress.APPLIED
        new_one.save()

        response = self._update(new_one, '22:30')

        new_one.refresh_from_db()
        self.assertEqual(new_one.confirmed_start_time, time(22, 30))
        self.assertEqual(new_one.admin_note, '入れ替え中')
        self.assertIsNotNone(new_one.schedule_confirmed_at)
        self.assertEqual(new_one.progress, VketParticipation.Progress.REHEARSAL)
        self.assertContains(response, '集会C の日程を確定しました。')
        self.assertContains(response, '他の集会と時間が重なっています')
        self.assertContains(response, '集会C（22:30〜23:30）と 集会B（22:00〜23:00）')

    def test_row_confirm_without_overlap_has_no_warning(self):
        """重なりが無ければ警告は出さない"""
        response = self._update(self.a, '20:00')

        self.assertContains(response, '集会A の日程を確定しました。')
        self.assertNotContains(response, '他の集会と時間が重なっています')

    def test_admin_can_swap_two_slots(self):
        """入れ替えの途中は重なっても保存でき、最後は両方の枠が入れ替わる"""
        self._update(self.a, '22:00')
        response = self._update(self.b, '21:00')

        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual(self.a.confirmed_start_time, time(22, 0))
        self.assertEqual(self.b.confirmed_start_time, time(21, 0))
        self.assertNotContains(response, '他の集会と時間が重なっています')

    def test_schedule_table_red_cells_follow_same_judgement(self):
        """日程表の赤いセルは警告と同じ判定（有効な参加だけ・間隔込み）で決まる"""
        url = reverse('vket:manage_schedule', kwargs={'pk': self.collaboration.pk})
        withdrawn = self._participation('集会D', time(21, 0))
        withdrawn.lifecycle = VketParticipation.Lifecycle.WITHDRAWN
        withdrawn.save(update_fields=['lifecycle'])

        context = self.client.get(url).context
        self.assertEqual(context['overlap_warnings'], [])
        self.assertFalse(any(cell['overlap'] for row in context['rows'] for cell in row['cells']))

        self.collaboration.settings_json = {'schedule_buffer_minutes': 10}
        self.collaboration.save(update_fields=['settings_json'])
        context = self.client.get(url).context
        red_rows = {
            row['participation'].community.name
            for row in context['rows']
            if any(cell['overlap'] for cell in row['cells'])
        }
        self.assertEqual(len(context['overlap_warnings']), 1)
        self.assertEqual(red_rows, {'集会A', '集会B'})

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
        self.assertContains(response, '重なりを承知で公開する')
        self.assertFalse(Event.objects.filter(community__in=[self.a.community, self.b.community]).exists())
        self.a.refresh_from_db()
        self.assertNotEqual(self.a.progress, VketParticipation.Progress.DONE)

    def test_publish_with_allow_overlap_flag_publishes_and_records_pairs(self):
        """「重なりを承知で公開する」を付けた時だけ公開し、承知した組をログと画面に残す"""
        self.b.confirmed_start_time = time(21, 30)
        self.b.save(update_fields=['confirmed_start_time'])
        self.collaboration.phase = VketCollaboration.Phase.LOCKED
        self.collaboration.save(update_fields=['phase'])

        with self.assertLogs('vket.views.publish', level='WARNING') as logs:
            response = self.client.post(
                reverse('vket:manage_publish', kwargs={'pk': self.collaboration.pk}),
                data={'allow_overlap': '1'},
                follow=True,
            )

        self.assertContains(response, '2件のイベントを公開しました')
        self.assertContains(response, '次の重なりを承知で公開しました')
        record = next(r for r in logs.records if '重なりを承知で公開' in r.getMessage())
        self.assertEqual(record.overlapping_participation_ids, [[self.a.pk, self.b.pk]])

    def test_manage_page_shows_publish_overlap_checkbox_only_with_overlap(self):
        """確定フェーズの管理画面は、重なりがある時だけ組と承知のチェックを出す"""
        self.collaboration.phase = VketCollaboration.Phase.LOCKED
        self.collaboration.save(update_fields=['phase'])
        url = reverse('vket:manage', kwargs={'pk': self.collaboration.pk})
        self.assertNotContains(self.client.get(url), 'name="allow_overlap"')

        self.b.confirmed_start_time = time(21, 30)
        self.b.save(update_fields=['confirmed_start_time'])
        response = self.client.get(url)
        self.assertContains(response, 'name="allow_overlap"')
        self.assertContains(response, '集会A（21:00〜22:00）と 集会B（21:30〜22:30）')

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
