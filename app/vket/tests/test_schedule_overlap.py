"""Vket コラボの発表時間の重なり警告（#705）のテスト."""

from datetime import time, timedelta
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from tests.factories import make_community, make_event, make_user
from vket.models import VketCollaboration, VketParticipation, VketPresentation
from vket.schedule import (
    ScheduleBlock,
    blocks_from,
    presentation_block_for,
    find_conflicting_pairs,
    blocks_conflict,
    busy_payload,
    find_conflicts,
    format_pair,
    get_schedule_buffer_minutes,
    set_schedule_buffer_minutes,
)

from ._vket_test_bases import VketApplyFlowBase


def _block(name: str, day, start: time, duration: int, community_id: int = 0) -> ScheduleBlock:
    return ScheduleBlock(
        participation_id=None, community_id=community_id, community_name=name,
        date=day, start=start, duration=duration,
    )


class BlocksConflictTests(TestCase):
    def setUp(self):
        self.day = timezone.localdate()
        self.next_day = self.day + timedelta(days=1)

    def test_adjacent_blocks_do_not_conflict_without_buffer(self):
        """終了と開始が接しているだけなら重ならない"""
        self.assertFalse(blocks_conflict(
            _block('A', self.day, time(21, 0), 60), _block('B', self.day, time(22, 0), 60),
        ))

    def test_buffer_makes_adjacent_blocks_conflict(self):
        """間隔を足すと、接している枠も重なりとみなす"""
        a = _block('A', self.day, time(21, 0), 60)
        self.assertTrue(blocks_conflict(a, _block('B', self.day, time(22, 0), 60), 15))
        self.assertFalse(blocks_conflict(a, _block('B', self.day, time(22, 15), 60), 15))

    def test_block_crossing_midnight_conflicts_with_next_day_block(self):
        """23:30 から 90 分の枠は、翌日 00:30 からの枠と重なる"""
        late = _block('深夜集会', self.day, time(23, 30), 90, 1)
        self.assertTrue(blocks_conflict(late, _block('朝集会', self.next_day, time(0, 30), 60, 2)))
        self.assertFalse(blocks_conflict(late, _block('朝集会', self.next_day, time(1, 0), 60, 2)))

    def test_buffer_applies_across_midnight(self):
        """前日 23:50 に終わる枠と翌日 00:00 からの枠は、間隔 15 分なら重なる"""
        late = _block('深夜集会', self.day, time(22, 50), 60, 1)
        early = _block('朝集会', self.next_day, time(0, 0), 60, 2)
        self.assertFalse(blocks_conflict(late, early))
        self.assertTrue(blocks_conflict(late, early, buffer_minutes=15))

    def test_format_pair_shows_partner_date(self):
        """相手が別の日の時は、相手の日付（翌日なら「翌」）を付ける"""
        late = _block('深夜集会', self.day, time(23, 30), 90, 1)
        self.assertIn(
            '朝集会（翌00:30〜01:30）',
            format_pair(late, _block('朝集会', self.next_day, time(0, 30), 60, 2)),
        )
        later = self.day + timedelta(days=2)
        self.assertIn(
            f'朝集会（{later.month}/{later.day} 00:30〜01:30）',
            format_pair(late, _block('朝集会', later, time(0, 30), 60, 2)),
        )
        self.assertIn(
            '昼集会（21:00〜22:00）',
            format_pair(late, _block('昼集会', self.day, time(21, 0), 60, 2)),
        )


class BusyPayloadTests(TestCase):
    def setUp(self):
        self.day = timezone.localdate()
        self.next_day = self.day + timedelta(days=1)

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

    def test_buffer_after_block_is_shown_on_next_day(self):
        """23:55 終了の枠は、間隔 10 分なら翌日の 00:05 まで埋まっていると表示する"""
        payload = busy_payload(
            [_block('深夜集会', self.day, time(23, 30), 25, 1)], buffer_minutes=10,
        )

        self.assertEqual(
            payload['days'][self.day.isoformat()], [{'index': 0, 'label': '23:20〜翌00:05'}],
        )
        self.assertEqual(
            payload['days'][self.next_day.isoformat()],
            [{'index': 0, 'label': '〜00:05（前日から）'}],
        )
        # JS の判定は前後に間隔を足すため、判定用の開始・終了は枠そのものを渡す
        self.assertEqual(payload['blocks'][0]['end_abs'] - payload['blocks'][0]['start_abs'], 25)

    def test_buffer_not_reaching_next_day_is_not_shown_there(self):
        """間隔を足しても翌日にかからない枠は、翌日の欄には出さない"""
        for duration in (19, 20):
            with self.subTest(duration=duration):
                payload = busy_payload(
                    [_block('夜集会', self.day, time(23, 30), duration, 1)], buffer_minutes=10,
                )
                self.assertNotIn(self.next_day.isoformat(), payload['days'])

    def test_buffer_before_block_is_shown_on_previous_day(self):
        """翌日 00:05 開始の枠は、間隔 10 分なら前日の 23:55 から表示する"""
        payload = busy_payload(
            [_block('朝集会', self.next_day, time(0, 5), 30, 1)], buffer_minutes=10,
        )

        self.assertEqual(
            payload['days'][self.day.isoformat()], [{'index': 0, 'label': '23:55〜翌00:45'}],
        )
        self.assertEqual(
            payload['days'][self.next_day.isoformat()],
            [{'index': 0, 'label': '〜00:45（前日から）'}],
        )

    def test_buffer_starting_at_midnight_does_not_touch_previous_day(self):
        """入れ替えの時間がちょうど 00:00 に始まる枠は、前日の欄には出さない"""
        payload = busy_payload(
            [_block('朝集会', self.next_day, time(0, 10), 30, 1)], buffer_minutes=10,
        )
        self.assertNotIn(self.day.isoformat(), payload['days'])


class BufferSettingTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            slug='vket-buffer-setting', name='設定確認', period_start=today,
            period_end=today + timedelta(days=7), registration_deadline=today,
            lt_deadline=today, settings_json={'stage_url': 'https://example.com/stage'},
        )

    def test_invalid_values_read_as_zero(self):
        """settings_json の不正値・dict でない値は既定値 0 を返す"""
        for value in (
            {'schedule_buffer_minutes': 'abc'}, None,
            ['schedule_buffer_minutes', 15], 'schedule_buffer_minutes',
        ):
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


class VketOverlapApplyBase(VketApplyFlowBase):
    """発表の重なりは保存後に警告する"""

    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.other = VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=make_community(name='ゲーム開発集会', status='approved'),
            requested_date=self.today, requested_start_time=time(21), requested_duration=120,
        )
        self.presentation = VketPresentation.objects.create(
            participation=self.other, requested_start_time=time(21, 30), duration=30,
        )
        self.client.force_login(self.owner)
        self._set_active_community()

    def _post_apply(self, lt_start='21:45', *, rows=None, date=None, start='21:00'):
        own = self._own_participation()
        data = {
            'requested_date': (date or self.today).isoformat(),
            'requested_start_time': start, 'requested_duration': '90',
            'organizer_note': '',
        }
        data.update(self._make_formset_data(
            rows if rows is not None else [{'speaker': '発表者', 'theme': '技術の話', 'lt_start_time': lt_start}],
            initial_forms=own.presentations.count() if own else 0,
        ))
        response = self.client.post(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}), data=data)
        # 実際のブラウザと同じく転送先で messages を消費し、次の保存へ持ち越さない。
        if response.status_code == 302:
            self.client.get(response.url)
        return response

    def _own_participation(self):
        return VketParticipation.objects.filter(collaboration=self.collaboration, community=self.community).first()

    def _warnings(self, response):
        return [str(m) for m in response.wsgi_request._messages if m.level == 30]

class VketApplyScheduleOverlapTests(VketOverlapApplyBase):
    def test_overlapping_presentations_save_with_warning(self):
        """重なる発表が保存でき、相手の集会名と発表時間で警告する"""
        response = self._post_apply()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._own_participation().presentations.get().requested_start_time, time(21, 45))
        warning = ' '.join(self._warnings(response))
        self.assertIn('発表時間が重なっています', warning)
        self.assertIn('ゲーム開発集会（21:30〜22:00）', warning)

    def test_overlapping_participation_slots_without_presentation_overlap_do_not_warn(self):
        """参加枠が重なっていても発表が接しているだけなら警告しない"""
        response = self._post_apply('22:00')
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self._warnings(response))

    def test_no_presentations_do_not_warn(self):
        response = self._post_apply(rows=[])
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self._warnings(response))

    def test_buffer_is_applied_to_presentations(self):
        self.collaboration.settings_json = {'schedule_buffer_minutes': 15}
        self.collaboration.save()
        self.assertTrue(self._warnings(self._post_apply('22:00')))
        self.assertFalse(self._warnings(self._post_apply('22:15')))

    def test_confirmed_presentation_time_and_participation_date_take_priority_independently(self):
        """参加の確定開始時刻がなくても、確定日と発表の確定時刻を使う"""
        tomorrow = self.today + timedelta(days=1)
        make_event(self.community, event_date=tomorrow, start_time='21:00', duration=120)
        self.other.confirmed_date = tomorrow
        self.other.save()
        self.presentation.confirmed_start_time = time(22)
        self.presentation.save()
        self.assertFalse(self._warnings(self._post_apply('22:00')))
        response = self._post_apply('22:00', date=tomorrow)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self._warnings(response))

    def test_inactive_and_deleted_presentations_do_not_warn(self):
        for lifecycle in (VketParticipation.Lifecycle.WITHDRAWN, VketParticipation.Lifecycle.DECLINED):
            self.other.lifecycle = lifecycle
            self.other.save()
            self.assertFalse(self._warnings(self._post_apply()))
        self.other.lifecycle = VketParticipation.Lifecycle.ACTIVE
        self.other.save()
        self.presentation.delete()
        self.assertFalse(self._warnings(self._post_apply()))

    def test_missing_time_is_not_replaced_with_participation_start(self):
        self.presentation.requested_start_time = None
        self.presentation.save()
        self.assertFalse(self._warnings(self._post_apply()))
        self.assertEqual(blocks_from([self.other]), [])

    def test_same_community_presentations_do_not_warn(self):
        self.presentation.delete()
        response = self._post_apply(rows=[
            {'speaker': 'A', 'theme': 'A', 'lt_start_time': '21:00'},
            {'speaker': 'B', 'theme': 'B', 'lt_start_time': '21:15'},
        ])
        self.assertFalse(self._warnings(response))
        self.assertEqual(self._own_participation().presentations.count(), 2)

    def test_presentation_only_update_warns_without_blocking_or_schedule_lock(self):
        self._post_apply('22:00')
        with mock.patch('django.db.models.QuerySet.select_for_update') as lock:
            response = self._post_apply('21:45')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self._warnings(response))
        lock.assert_not_called()

    def test_presentation_crossing_midnight_warns(self):
        tomorrow = self.today + timedelta(days=1)
        make_event(self.community, event_date=tomorrow, start_time='00:00', duration=120)
        self.presentation.requested_start_time = time(23, 50)
        self.presentation.save()
        response = self._post_apply('00:00', date=tomorrow, start='00:00')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self._warnings(response))

    def test_busy_payload_uses_presentation_duration_and_reuses_loaded_rows(self):
        with mock.patch('vket.schedule.active_blocks') as reread:
            response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))
        reread.assert_not_called()
        busy = response.context['busy_payload']
        self.assertEqual(busy['days'][self.today.isoformat()], [{'index': 0, 'label': '21:30〜22:00'}])
        self.assertEqual(busy['blocks'][0]['end_abs'] - busy['blocks'][0]['start_abs'], 30)
        self.assertContains(response, 'このまま保存できます。')

    def test_busy_and_schedule_keep_presentation_when_participation_start_is_missing(self):
        """発表時刻さえあれば、参加枠の開始時刻が無くても発表時間を表示する"""
        self.other.requested_start_time = None
        self.other.confirmed_date = self.today + timedelta(days=1)
        self.other.save()
        response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))
        self.assertIn(self.other.confirmed_date.isoformat(), response.context['busy_payload']['days'])
        row = next(r for r in response.context['rows'] if r['participation'].pk == self.other.pk)
        self.assertEqual(row['date'], self.other.confirmed_date)
        self.assertEqual(row['presentation_ranges'], ['21:30〜22:00'])

    def test_busy_uses_each_presentations_actual_duration(self):
        self.other.lt_slot_minutes = 15
        self.other.save()
        self.presentation.duration = 45
        self.presentation.save()
        response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))
        self.assertEqual(response.context['busy_payload']['days'][self.today.isoformat()], [
            {'index': 0, 'label': '21:30〜22:15'},
        ])
        self.assertContains(response, '発表: 21:30〜22:15')

    def test_busy_panel_is_available_when_only_presentation_editing_is_allowed(self):
        self._post_apply('22:00')
        own = self._own_participation()
        own.confirmed_date = self.today
        own.confirmed_start_time = time(21)
        own.confirmed_duration = 120
        own.save()
        response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))
        self.assertFalse(response.context['permissions'].can_edit_schedule)
        self.assertTrue(response.context['busy_payload']['blocks'])
        self.assertContains(response, 'id="busy-slots"')
        self.assertContains(response, f'data-confirmed-date="{self.today.isoformat()}"')

    def test_deleted_own_presentation_does_not_leave_stale_warning(self):
        self._post_apply()
        response = self._post_apply(rows=[{'speaker': '発表者', 'theme': '技術の話', 'DELETE': True}])
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self._warnings(response))
        self.assertFalse(self._own_participation().presentations.exists())

    def test_busy_script_checks_delete_checkbox_and_keeps_requested_time_for_fill(self):
        """確定時刻が異なっても、補完用の希望時刻と削除の checked を画面へ出す"""
        self._post_apply('21:00')
        presentation = self._own_participation().presentations.get()
        presentation.confirmed_start_time = time(22)
        presentation.save()
        response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))
        self.assertContains(response, 'data-confirmed-start="22:00"')
        self.assertEqual(response.context['formset'].forms[0]['lt_start_time'].value(), time(21))
        self.assertContains(response, 'deleted && deleted.checked')
        self.assertContains(response, 'deleteInput.checked = true;')
        self.assertContains(response, 'var requestedStart = input ? toMinutes(input.value) : null;')
        self.assertContains(response, 'previous = requestedStart;')
        self.assertContains(response, 'if (startOfDay === null) startOfDay = requestedStart;')

    def test_missing_time_after_confirmed_row_uses_requested_time_on_save(self):
        """前行の希望 21:00・確定 22:00 なら、次行の空欄は希望 21:30 になる"""
        self._post_apply('21:00')
        presentation = self._own_participation().presentations.get()
        presentation.confirmed_start_time = time(22)
        presentation.save()
        response = self._post_apply(rows=[
            {'speaker': '発表者', 'theme': '技術の話', 'lt_start_time': '21:00'},
            {'speaker': '追加者', 'theme': '追加の話', 'lt_start_time': ''},
        ])
        self.assertEqual(response.status_code, 302)
        added = self._own_participation().presentations.get(speaker='追加者')
        self.assertEqual(added.requested_start_time, time(21, 30))


class VketAdminScheduleOverlapTests(TestCase):
    """運営の確定・公開同期も発表時間で警告するだけで進める"""

    def setUp(self):
        self.today = timezone.localdate()
        self.collaboration = VketCollaboration.objects.create(
            slug='overlap-admin', name='コラボ', period_start=self.today,
            period_end=self.today + timedelta(days=7), registration_deadline=self.today,
            lt_deadline=self.today, phase=VketCollaboration.Phase.LOCKED,
        )
        self.a = self._participation('集会A', time(21))
        self.b = self._participation('集会B', time(22))
        self.client.force_login(make_user(is_staff=True, is_superuser=True))

    def _participation(self, name, start):
        p = VketParticipation.objects.create(
            collaboration=self.collaboration, community=make_community(name=name, status='approved'),
            confirmed_date=self.today, confirmed_start_time=start, confirmed_duration=120,
        )
        VketPresentation.objects.create(
            participation=p, speaker=name, theme='発表', requested_start_time=start,
            status=VketPresentation.Status.CONFIRMED,
        )
        return p

    def _update(self, **extra):
        return self.client.post(reverse('vket:manage_participation_update', kwargs={
            'pk': self.collaboration.pk, 'participation_id': self.a.pk,
        }), data={
            'lifecycle': 'active', 'confirmed_date': self.today.isoformat(),
            'confirmed_start_time': '21:00', 'confirmed_duration': '120', **extra,
        }, follow=True)

    def _overlap(self):
        p = self.b.presentations.get()
        p.confirmed_start_time = time(21, 15)
        p.save()

    def test_confirm_without_acknowledgement_saves_and_warns(self):
        self._overlap()
        response = self._update()
        self.assertEqual(response.status_code, 200)
        self.a.refresh_from_db()
        self.assertIsNotNone(self.a.schedule_confirmed_at)
        self.assertIsNotNone(self.a.published_event_id)
        self.assertContains(response, '発表時間が重なっています')
        self.assertNotContains(response, '重なりを承知で')

    def test_confirmation_compares_posted_presentation_time(self):
        """参加枠ではなく今回入力した発表時刻で警告する"""
        p = self.a.presentations.get()
        response = self._update(**{f'pres_{p.pk}_start_time': '22:15'})
        self.assertContains(response, '発表時間が重なっています')
        p.refresh_from_db()
        self.assertEqual(p.confirmed_start_time, time(22, 15))

    def test_confirmation_of_only_participation_overlap_has_no_warning(self):
        response = self._update()
        self.assertNotContains(response, '発表時間が重なっています')

    def test_publish_without_acknowledgement_succeeds_and_warns(self):
        self._overlap()
        response = self.client.post(reverse('vket:manage_publish', kwargs={'pk': self.collaboration.pk}), follow=True)
        self.assertContains(response, '公開処理完了: 2件')
        self.assertContains(response, '発表時間が重なっています')
        self.assertNotContains(response, '重なりを承知で')
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertIsNotNone(self.a.published_event_id)
        self.assertIsNotNone(self.b.published_event_id)

    def test_manage_page_warns_without_checkbox_or_signature(self):
        self._overlap()
        response = self.client.get(reverse('vket:manage', kwargs={'pk': self.collaboration.pk}))
        self.assertContains(response, 'data-testid="publish-overlap-warning"')
        self.assertNotContains(response, 'name="allow_overlap"')
        self.assertNotContains(response, 'name="overlap_signature"')

    def test_schedule_red_cells_are_only_overlapping_presentation_times(self):
        self._overlap()
        response = self.client.get(reverse('vket:manage_schedule', kwargs={'pk': self.collaboration.pk}))
        self.assertTrue(response.context['overlap_warnings'])
        row = next(r for r in response.context['rows'] if r['participation'].pk == self.a.pk)
        red_times = [slot.start for slot, cell in zip(response.context['slots'], row['cells']) if cell['overlap']]
        self.assertEqual(red_times, [time(21)])
        self.assertContains(response, '重複（発表時間）')

    def test_same_half_hour_cell_without_actual_overlap_is_not_red(self):
        a = self.a.presentations.get()
        a.duration = 10
        a.save()
        b = self.b.presentations.get()
        b.requested_start_time = time(21, 15)
        b.duration = 10
        b.save()
        response = self.client.get(reverse('vket:manage_schedule', kwargs={'pk': self.collaboration.pk}))
        self.assertFalse(response.context['overlap_warnings'])
        self.assertFalse(any(c['overlap'] or c['lt_overlap'] for r in response.context['rows'] for c in r['cells']))

    def test_manage_overlap_context_reuses_prefetched_presentations(self):
        self._overlap()
        from vket.views.manage import ManageView
        participations = list(self.collaboration.participations.select_related('community').prefetch_related('presentations'))
        with CaptureQueriesContext(connection) as queries:
            context = ManageView._publish_overlap_context(self.collaboration, participations)
        self.assertTrue(context['publish_overlap_pairs'])
        self.assertEqual(len(queries), 0)

    def test_find_conflicts_skips_declined_and_same_community(self):
        self._overlap()
        candidate = presentation_block_for(self.a, self.a.presentations.get())
        self.assertEqual(len(find_conflicts(self.collaboration, candidate)), 1)
        self.b.lifecycle = VketParticipation.Lifecycle.DECLINED
        self.b.save()
        self.assertFalse(find_conflicts(self.collaboration, candidate))
        self.assertFalse(find_conflicting_pairs(blocks_from([self.a, self.b])))

    def test_buffer_setting_saves_without_changing_other_keys(self):
        self.collaboration.settings_json = {'stage_url': 'https://example.com/stage'}
        self.collaboration.save()
        response = self.client.post(reverse('vket:manage_schedule_settings', kwargs={'pk': self.collaboration.pk}), {
            'schedule_buffer_minutes': '15',
        })
        self.assertEqual(response.status_code, 302)
        self.collaboration.refresh_from_db()
        self.assertEqual(self.collaboration.settings_json, {'stage_url': 'https://example.com/stage', 'schedule_buffer_minutes': 15})
