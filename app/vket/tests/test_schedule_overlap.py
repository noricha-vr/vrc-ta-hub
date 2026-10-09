"""Vket コラボの参加枠の重なり判定（#695）のテスト."""

from contextlib import contextmanager
from datetime import time, timedelta
from unittest import mock

from django.db import connection
from django.db.models import QuerySet
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
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


@contextmanager
def _spy_select_for_update(on_lock=None):
    """select_for_update の呼び出しを記録する（テスト DB はロックを持たないため）"""
    original = QuerySet.select_for_update

    def call(qs, *args, **kwargs):
        if on_lock is not None and qs.model is VketCollaboration:
            on_lock()
        return original(qs, *args, **kwargs)

    with mock.patch.object(QuerySet, 'select_for_update', autospec=True) as spy:
        spy.side_effect = call
        yield spy


def _locked_collaboration(spy) -> bool:
    return any(c.args[0].model is VketCollaboration for c in spy.call_args_list)


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

    def _post_apply(self, start: str, duration: str = '60', on_date=None):
        data = {
            'requested_date': (on_date or self.today).isoformat(),
            'requested_start_time': start,
            'requested_duration': duration,
            'organizer_note': '',
        }
        data.update(self._make_formset_data([]))
        return self.client.post(
            reverse('vket:apply', kwargs={'pk': self.collaboration.pk}), data=data,
        )

    def _own_participation(self):
        return VketParticipation.objects.filter(
            collaboration=self.collaboration, community=self.community,
        ).first()

    def _make_own(self, start: str = '21:00', **extra) -> VketParticipation:
        return VketParticipation.objects.create(
            collaboration=self.collaboration,
            community=self.community,
            requested_date=self.today,
            requested_start_time=start,
            requested_duration=60,
            progress=VketParticipation.Progress.APPLIED,
            **extra,
        )

    def test_organizer_cannot_save_overlapping_time(self):
        """他の集会と重なる時間では保存できず、集会名つきで知らせる"""
        response = self._post_apply('21:30')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'その時間はゲーム開発集会が申込み済みです。')
        self.assertIsNone(self._own_participation())

    def test_organizer_can_move_to_free_time(self):
        """空いている時間には申し込め、後から別の空き時間へ動かせる"""
        self.assertEqual(self._post_apply('22:00').status_code, 302)

        self.assertEqual(self._post_apply('23:00').status_code, 302)
        self.assertEqual(self._own_participation().requested_start_time, time(23, 0))

    def test_own_slot_does_not_block_itself(self):
        """自分の枠と重なる時間へ動かしても、自分自身では弾かれない"""
        self.assertEqual(self._post_apply('22:00').status_code, 302)

        self.assertEqual(self._post_apply('22:30').status_code, 302)
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
        self._own_participation().delete()
        response = self._post_apply('23:30')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ゲーム開発集会が申込み済みです')

    def test_withdrawn_participation_is_not_counted(self):
        """辞退・不参加の参加は重なりの相手に含めない"""
        self.other.lifecycle = VketParticipation.Lifecycle.WITHDRAWN
        self.other.save(update_fields=['lifecycle'])

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

        with _spy_select_for_update() as spy:
            first = self._post_apply('22:00')
        self.assertEqual(first.status_code, 302)
        self.assertTrue(_locked_collaboration(spy), 'コラボの行を select_for_update でロックしていない')

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

    def test_lt_only_update_is_not_blocked_and_does_not_lock(self):
        """希望の時間を変えない保存（発表情報だけの更新）は、既存の重なりで止めず、ロックもしない"""
        self._make_own('21:00')

        with _spy_select_for_update() as spy:
            response = self._post_apply('21:00')

        self.assertEqual(response.status_code, 302)
        self.assertFalse(_locked_collaboration(spy))

    def test_admin_save_is_not_judged_and_does_not_lock(self):
        """管理者が申込み画面から保存する時は判定もロックもしない"""
        self.other_user.is_staff = True
        self.other_user.save(update_fields=['is_staff'])
        self.client.force_login(self.other_user)
        self._set_active_community()

        with _spy_select_for_update() as spy:
            response = self._post_apply('21:30')

        self.assertEqual(response.status_code, 302)
        self.assertFalse(_locked_collaboration(spy))
        self.assertEqual(self._own_participation().requested_start_time, time(21, 30))

    def test_withdrawn_own_participation_is_not_judged(self):
        """自分の参加が辞退・不参加の時は重なりを判定しない"""
        own = self._make_own('23:00', lifecycle=VketParticipation.Lifecycle.WITHDRAWN)

        with _spy_select_for_update() as spy:
            response = self._post_apply('21:30')

        self.assertEqual(response.status_code, 302)
        self.assertFalse(_locked_collaboration(spy))
        own.refresh_from_db()
        self.assertEqual(own.requested_start_time, time(21, 30))

    def test_change_check_uses_values_reread_after_lock(self):
        """希望が変わったかは、ロック後に読み直した参加の値で判定する"""
        own = self._make_own('23:00')

        def concurrent_update():
            # ロックを待つ間に、同じ集会の別の保存が 21:30 を書き込んで確定した
            VketParticipation.objects.filter(pk=own.pk).update(requested_start_time=time(21, 30))

        with _spy_select_for_update(on_lock=concurrent_update) as spy:
            response = self._post_apply('21:30')

        # 読み直した値では希望は変わっていないので、重なりの判定はせずに保存を通す
        self.assertTrue(_locked_collaboration(spy))
        self.assertEqual(response.status_code, 302)

    def test_conflict_render_uses_buffer_reread_after_lock(self):
        """ロック待ち中に間隔が増えた時は、重複エラーの再表示にも新しい間隔を使う"""
        tomorrow = self.today + timedelta(days=1)
        make_event(
            self.community, event_date=tomorrow, start_time='00:00', duration=60, weekday='',
            accepts_lt_application=True,
        )
        self.other.requested_start_time = time(23, 30)
        self.other.requested_duration = 25
        self.other.save()

        def concurrent_update():
            VketCollaboration.objects.filter(pk=self.collaboration.pk).update(
                settings_json={'schedule_buffer_minutes': 10},
            )

        with _spy_select_for_update(on_lock=concurrent_update) as spy:
            response = self._post_apply('00:00', on_date=tomorrow)

        self.assertTrue(_locked_collaboration(spy))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '前後 10 分は入れ替えの時間として空けてください。')
        self.assertEqual(response.context['schedule_buffer_minutes'], 10)
        self.assertEqual(get_schedule_buffer_minutes(response.context['collaboration']), 10)
        self.assertContains(response, 'data-buffer-minutes="10"')
        self.assertEqual(
            response.context['busy_payload']['days'][tomorrow.isoformat()],
            [{'index': 0, 'label': '〜00:05（前日から）'}],
        )
        self.assertIsNone(self._own_participation())

    def test_conflict_render_shows_previous_day_block_with_buffer(self):
        """前日の枠が間隔込みで翌日にかかる時は、選んだ翌日の埋まっている時間に出す"""
        tomorrow = self.today + timedelta(days=1)
        make_event(
            self.community, event_date=tomorrow, start_time='00:00', duration=60, weekday='',
            accepts_lt_application=True,
        )
        self.collaboration.settings_json = {'schedule_buffer_minutes': 10}
        self.collaboration.save(update_fields=['settings_json'])
        self.other.requested_start_time = time(23, 30)
        self.other.requested_duration = 25
        self.other.save()

        response = self._post_apply('00:00', on_date=tomorrow)

        self.assertContains(response, 'その時間はゲーム開発集会が申込み済みです。')
        self.assertEqual(
            response.context['busy_payload']['days'][tomorrow.isoformat()],
            [{'index': 0, 'label': '〜00:05（前日から）'}],
        )
        self.assertEqual(response.context['busy_payload']['blocks'][0]['name'], 'ゲーム開発集会')
        self.assertIsNone(self._own_participation())

    def test_apply_page_omits_previous_day_block_not_reaching_next_day(self):
        """前日の枠が間隔込みでも翌日に届かない時は、翌日の欄には出さない"""
        tomorrow = self.today + timedelta(days=1)
        self.collaboration.settings_json = {'schedule_buffer_minutes': 10}
        self.collaboration.save(update_fields=['settings_json'])
        self.other.requested_start_time = time(23, 30)
        self.other.requested_duration = 20
        self.other.save()

        response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(tomorrow.isoformat(), response.context['busy_payload']['days'])

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
        self._make_own(
            '22:00', confirmed_date=self.today, confirmed_start_time='22:00', confirmed_duration=60,
        )

        with mock.patch('vket.views.apply.busy_payload') as build:
            response = self.client.get(reverse('vket:apply', kwargs={'pk': self.collaboration.pk}))

        self.assertEqual(response.status_code, 200)
        build.assert_not_called()
        self.assertIsNone(response.context['busy_payload'])
        self.assertNotContains(response, 'id="busy-slots"')


class VketAdminScheduleOverlapTests(TestCase):
    """運営の確定・公開同期は、確定済みの枠との重なりを承知した時だけ通す"""

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

    def _participation(self, name: str, start: time, *, confirmed: bool = True) -> VketParticipation:
        community = make_community(name=name, status='approved', frequency='毎週', weekdays=[], organizers='')
        values = (
            {'confirmed_date': self.today, 'confirmed_start_time': start, 'confirmed_duration': 60,
             'schedule_confirmed_at': timezone.now(), 'progress': VketParticipation.Progress.REHEARSAL}
            if confirmed else
            {'requested_date': self.today, 'requested_start_time': start, 'requested_duration': 60,
             'progress': VketParticipation.Progress.APPLIED}
        )
        return VketParticipation.objects.create(
            collaboration=self.collaboration, community=community, **values,
        )

    def _update(self, participation: VketParticipation, start: str, **extra):
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
                **extra,
            },
            follow=True,
        )

    def _publish(self, **data):
        self.collaboration.phase = VketCollaboration.Phase.LOCKED
        self.collaboration.save(update_fields=['phase'])
        return self.client.post(
            reverse('vket:manage_publish', kwargs={'pk': self.collaboration.pk}), data=data, follow=True,
        )

    def _make_publish_overlap(self):
        self.b.confirmed_start_time = time(21, 30)
        self.b.save(update_fields=['confirmed_start_time'])
        return f'{min(self.a.pk, self.b.pk)}-{max(self.a.pk, self.b.pk)}'

    def _set_buffer_while_waiting_for_lock(self, minutes: int):
        """ロックを待つ間に、別のリクエストが入れ替えの間隔を変えたことにする"""
        def change():
            VketCollaboration.objects.filter(pk=self.collaboration.pk).update(
                settings_json={'stage_url': 'https://example.com/stage', 'schedule_buffer_minutes': minutes},
            )
        return change

    def test_row_confirm_uses_buffer_read_after_lock(self):
        """ロックを待つ間に間隔が増えたら、ロック後の間隔で判定して隣の枠との確定を止める"""
        new_one = self._participation('集会C', time(23, 0), confirmed=False)

        with _spy_select_for_update(on_lock=self._set_buffer_while_waiting_for_lock(15)):
            response = self._update(new_one, '23:00')

        self.assertContains(response, '重なりを承知で確定する')
        new_one.refresh_from_db()
        self.assertIsNone(new_one.confirmed_start_time)

    def test_publish_uses_buffer_read_after_lock(self):
        """ロックを待つ間に間隔が増えたら、ロック後の間隔で隣り合う確定済みの枠を重なりとして止める"""
        with _spy_select_for_update(on_lock=self._set_buffer_while_waiting_for_lock(15)):
            response = self._publish()

        self.assertContains(response, '重なりを承知で公開する')
        self.assertFalse(Event.objects.filter(community__in=[self.a.community, self.b.community]).exists())

    def test_row_confirm_with_confirmed_overlap_is_blocked_without_acknowledgement(self):
        """確定済みの枠と重なる確定は、承知が無ければ何も保存せず、組と承知のチェックを出す"""
        new_one = self._participation('集会C', time(23, 0), confirmed=False)

        response = self._update(new_one, '22:30')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '集会C（22:30〜23:30）と 集会B（22:00〜23:00）')
        self.assertContains(response, '重なりを承知で確定する')
        self.assertContains(response, f'name="overlap_signature" value="{self.b.pk}"')
        self.assertContains(response, 'name="admin_note" value="入れ替え中"')
        new_one.refresh_from_db()
        self.assertIsNone(new_one.confirmed_start_time)
        self.assertIsNone(new_one.schedule_confirmed_at)
        self.assertIsNone(new_one.published_event_id)
        self.assertEqual(new_one.admin_note, '')

    def test_row_confirm_with_acknowledgement_confirms_and_records_pairs(self):
        """承知のチェックと、画面に出した組を付けて送った時だけ確定し、組をログと画面に残す"""
        new_one = self._participation('集会C', time(23, 0), confirmed=False)

        with self.assertLogs('vket.views.overlap', level='WARNING') as logs, \
                _spy_select_for_update() as spy:
            response = self._update(
                new_one, '22:30', allow_overlap='1', overlap_signature=str(self.b.pk),
            )

        new_one.refresh_from_db()
        self.assertEqual(new_one.confirmed_start_time, time(22, 30))
        self.assertIsNotNone(new_one.schedule_confirmed_at)
        self.assertEqual(new_one.progress, VketParticipation.Progress.REHEARSAL)
        self.assertIsNotNone(new_one.published_event_id)
        self.assertTrue(_locked_collaboration(spy))
        self.assertContains(response, '集会C の日程を確定しました。')
        self.assertContains(response, '次の重なりを承知で確定しました')
        record = next(r for r in logs.records if '重なりを承知で' in r.getMessage())
        self.assertEqual(record.overlap_pairs, ['%s 集会C（22:30〜23:30）と 集会B（22:00〜23:00）'
                                                % self.today.strftime('%Y/%m/%d')])
        self.assertEqual(record.participation_id, new_one.pk)

    def test_row_confirm_with_stale_acknowledgement_asks_again(self):
        """承知した組と今の重なりの組が違う時は、新しい組を出して再確認させる"""
        new_one = self._participation('集会C', time(23, 0), confirmed=False)
        d = self._participation('集会D', time(22, 0))

        response = self._update(
            new_one, '22:30', allow_overlap='1', overlap_signature=str(self.b.pk),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '確認した後に重なりの組が変わりました')
        self.assertContains(response, '集会D')
        self.assertContains(response, f'value="{",".join(str(p) for p in sorted([self.b.pk, d.pk]))}"')
        new_one.refresh_from_db()
        self.assertIsNone(new_one.schedule_confirmed_at)

    def test_row_confirm_with_requested_only_overlap_confirms_and_warns(self):
        """希望だけ（未確定）の枠との重なりは止めず、「未確定の申込みと重なっています」とだけ出す"""
        self._participation('集会C', time(20, 30), confirmed=False)

        response = self._update(self.a, '20:00')

        self.a.refresh_from_db()
        self.assertEqual(self.a.confirmed_start_time, time(20, 0))
        self.assertContains(response, '集会A の日程を確定しました。')
        self.assertContains(response, '未確定の申込みと重なっています')
        self.assertNotContains(response, '公開同期は')
        self.assertNotContains(response, '重なりを承知で確定する')

    def test_row_confirm_without_overlap_has_no_warning(self):
        """重なりが無ければ確認も警告も出さない"""
        response = self._update(self.a, '20:00')

        self.assertContains(response, '集会A の日程を確定しました。')
        self.assertNotContains(response, '重なって')

    def test_admin_can_swap_two_slots_with_acknowledgement(self):
        """入れ替えは、1 手目を承知で確定し、2 手目で重なりが解ける"""
        self._update(self.a, '22:00', allow_overlap='1', overlap_signature=str(self.b.pk))
        response = self._update(self.b, '21:00')

        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual(self.a.confirmed_start_time, time(22, 0))
        self.assertEqual(self.b.confirmed_start_time, time(21, 0))
        self.assertContains(response, '集会B の日程を確定しました。')
        self.assertNotContains(response, '重なって')

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

    def test_publish_is_blocked_without_acknowledgement(self):
        """公開同期は確定済みの枠どうしの重なりがあると、承知が無ければ公開せず組を出す"""
        signature = self._make_publish_overlap()

        response = self._publish()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '集会A（21:00〜22:00）と 集会B（21:30〜22:30）')
        self.assertContains(response, '重なりを承知で公開する')
        self.assertContains(response, f'name="overlap_signature" value="{signature}"')
        self.assertFalse(Event.objects.filter(community__in=[self.a.community, self.b.community]).exists())
        self.a.refresh_from_db()
        self.assertNotEqual(self.a.progress, VketParticipation.Progress.DONE)

    def test_publish_with_acknowledgement_publishes_and_records_pairs(self):
        """画面に出した組を承知した時だけ公開し、承知した組をログと画面に残す"""
        signature = self._make_publish_overlap()

        with self.assertLogs('vket.views.overlap', level='WARNING') as logs, \
                _spy_select_for_update() as spy:
            response = self._publish(allow_overlap='1', overlap_signature=signature)

        self.assertTrue(_locked_collaboration(spy))
        self.assertContains(response, '2件のイベントを公開しました')
        self.assertContains(response, '次の重なりを承知で公開しました')
        record = next(r for r in logs.records if '重なりを承知で' in r.getMessage())
        self.assertEqual(len(record.overlap_pairs), 1)
        self.assertEqual(record.collaboration_id, self.collaboration.pk)

    def test_publish_with_stale_acknowledgement_asks_again(self):
        """承知した組が今の組と違えば、公開せず新しい組で再確認させる"""
        self._make_publish_overlap()

        response = self._publish(allow_overlap='1', overlap_signature='0-0')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '確認した後に重なりの組が変わりました')
        self.assertFalse(Event.objects.filter(community__in=[self.a.community, self.b.community]).exists())

    def test_publish_ignores_requested_only_overlap(self):
        """公開同期は確定済みの組だけを見る（希望だけの枠との重なりでは止めない）"""
        self._participation('集会C', time(21, 0), confirmed=False)

        response = self._publish()

        self.assertContains(response, '2件のイベントを公開しました')

    def test_manage_page_shows_publish_overlap_checkbox_only_with_overlap(self):
        """確定フェーズの管理画面は、重なりがある時だけ組・承知のチェック・組の印を出す"""
        self.collaboration.phase = VketCollaboration.Phase.LOCKED
        self.collaboration.save(update_fields=['phase'])
        url = reverse('vket:manage', kwargs={'pk': self.collaboration.pk})
        self.assertNotContains(self.client.get(url), 'name="allow_overlap"')

        signature = self._make_publish_overlap()
        response = self.client.get(url)
        self.assertContains(response, 'name="allow_overlap"')
        self.assertContains(response, f'name="overlap_signature" value="{signature}"')
        self.assertContains(response, '集会A（21:00〜22:00）と 集会B（21:30〜22:30）')

    def test_manage_page_overlap_check_adds_no_queries(self):
        """管理画面の重なりの計算は読み込み済みの参加から行い、クエリを増やさない"""
        url = reverse('vket:manage', kwargs={'pk': self.collaboration.pk})
        self._make_publish_overlap()
        with CaptureQueriesContext(connection) as before:
            self.client.get(url)

        self.collaboration.phase = VketCollaboration.Phase.LOCKED
        self.collaboration.save(update_fields=['phase'])
        with CaptureQueriesContext(connection) as after:
            response = self.client.get(url)

        self.assertContains(response, 'name="allow_overlap"')
        self.assertEqual(len(after.captured_queries), len(before.captured_queries))

    def test_publish_runs_when_no_overlap(self):
        """重なりが無ければ公開同期を実行する"""
        response = self._publish()

        self.assertContains(response, '2件のイベントを公開しました')
        self.assertNotContains(response, '重なりを承知で')

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
        candidate = _block('候補', self.today, time(21, 30), 30, community_id=-1)
        self.assertEqual([b.community_name for b in find_conflicts(self.collaboration, candidate)], ['集会A'])

        self.a.lifecycle = VketParticipation.Lifecycle.DECLINED
        self.a.save(update_fields=['lifecycle'])
        self.assertEqual(find_conflicts(self.collaboration, candidate), [])
        self.assertTrue(block_for(self.b).is_confirmed)
