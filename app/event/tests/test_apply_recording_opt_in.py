"""撮影のオプトイン移行の優先規則・報告・更新時の保護。"""

import json
from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models.query import QuerySet
from django.test import TestCase
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from community.models import Community
from event.models import Event, EventDetail
from tests.factories import make_community, make_event, make_event_detail

RecordingPolicy = EventDetail.RecordingPolicy
COMMAND_MODULE = 'event.management.commands.apply_recording_opt_in'


class ApplyRecordingOptInTest(TestCase):
    def setUp(self):
        self.today = date(2026, 10, 9)
        self.cutoff = parse_datetime('2026-09-28T16:57:12+00:00')
        self.keep = make_community(name='テスト集会 A', recording_allowed=True)
        self.keep_second = make_community(name='テスト集会 B', recording_allowed=False, status='closed')
        self.other = make_community(name='テスト集会 C', recording_allowed=True, status='closed')
        self.disallowed = make_community(name='テスト集会 D', recording_allowed=False)
        self.events = {}

        # a: 今日を含む。既定値の public でも未来は b の対象にならない。
        self.a_public = self._detail(self.other, 0)
        self.a_allowed = self._detail(self.other, 1, policy=RecordingPolicy.ALLOWED, url=None)
        self.a_deleted = self._detail(self.other, 0, old=True, deleted_at=timezone.now())
        self.a_disallowed = self._detail(self.disallowed, 0, policy=RecordingPolicy.ALLOWED)
        self.a_rows = [self.a_public, self.a_allowed, self.a_deleted, self.a_disallowed]

        # b: 残す集会や URL の有無に関係なく、過去の既定値 public を allowed にする。
        self.b_keep = self._detail(self.keep, -1, old=True)
        self.b_keep_second = self._detail(self.keep_second, -1, old=True, url='https://example.com/video/1')
        self.b_other_with_url = self._detail(self.other, -1, old=True, url='https://example.com/video/2')
        self.b_rows = [self.b_keep, self.b_keep_second, self.b_other_with_url]

        # c: cutoff と同時刻は明示扱い。「動画撮影」の回答があれば古くても既定値扱いしない。
        self.c_public = self._detail(self.other, -1, created_at=self.cutoff)
        self.c_allowed = self._detail(self.other, -1, policy=RecordingPolicy.ALLOWED, url=None)
        self.c_answered = self._detail(self.other, -1, old=True, info='【動画撮影】公開を希望')
        self.c_rows = [self.c_public, self.c_allowed, self.c_answered]

        # d: 残す集会のこれからの発表・明示 public、URL あり、既に禁止など。
        self.d_keep_allowed = self._detail(self.keep, 0, policy=RecordingPolicy.ALLOWED)
        self.d_keep_public = self._detail(self.keep, 0, old=True)
        self.d_keep_explicit = self._detail(self.keep, -1)
        self.d_keep_answered = self._detail(self.keep_second, -1, old=True, info='動画撮影は許可')
        self.d_explicit_url = self._detail(self.other, -1, url='https://example.com/video/3')
        self.d_future_url = self._detail(self.other, 1, policy=RecordingPolicy.ALLOWED, url='https://example.com/video/4')
        self.d_future_forbidden_url = self._detail(self.other, 1, policy=RecordingPolicy.FORBIDDEN, url='https://example.com/video/5')
        self.d_deleted_url = self._detail(
            self.other, -1, policy=RecordingPolicy.ALLOWED,
            url='https://example.com/video/6', deleted_at=timezone.now(),
        )
        self.d_today_forbidden = self._detail(self.other, 0, policy=RecordingPolicy.FORBIDDEN)
        self.d_past_forbidden = self._detail(self.other, -1, policy=RecordingPolicy.FORBIDDEN, old=True)
        self.d_rows = [
            self.d_keep_allowed, self.d_keep_public, self.d_keep_explicit, self.d_keep_answered,
            self.d_explicit_url, self.d_future_url, self.d_future_forbidden_url,
            self.d_deleted_url, self.d_today_forbidden, self.d_past_forbidden,
        ]

    def _event(self, community, days):
        key = (community.pk, days)
        if key not in self.events:
            self.events[key] = make_event(community, event_date=self.today + timedelta(days=days))
        return self.events[key]

    def _detail(self, community, days, *, policy=RecordingPolicy.PUBLIC, url='', old=False, info='', created_at=None, **extra):
        detail = make_event_detail(
            self._event(community, days), recording_policy=policy,
            youtube_url=url, additional_info=info, **extra,
        )
        # auto_now_add は create 時の値を上書きするため、作成後に日時を直す。
        timestamp = created_at if created_at is not None else self.cutoff + timedelta(seconds=-1 if old else 1)
        EventDetail.all_objects.filter(pk=detail.pk).update(created_at=timestamp)
        return detail

    def _run(self, *args, ids=None):
        out = StringIO()
        keep_ids = ids if ids is not None else [self.keep.pk, self.keep_second.pk]
        id_args = [arg for pk in keep_ids for arg in ('--keep-community-id', str(pk))]
        with patch(f'{COMMAND_MODULE}.timezone.localdate', return_value=self.today):
            call_command('apply_recording_opt_in', *id_args, *args, stdout=out)
        return out.getvalue()

    def _backup(self, output):
        lines = [line for line in output.splitlines() if line.startswith('RECORDING_OPT_IN_BACKUP ')]
        self.assertEqual(len(lines), 1)
        return json.loads(lines[0].split(' ', 1)[1])

    def _state(self):
        return (
            list(Community._base_manager.order_by('pk').values()),
            list(Event.objects.order_by('pk').values()),
            list(EventDetail.all_objects.order_by('pk').values()),
        )

    def _assert_policy(self, detail, policy):
        detail.refresh_from_db()
        self.assertEqual(detail.recording_policy, policy)

    def _detail_line(self, detail, *, old=False):
        label = '旧 recording_policy' if old else 'recording_policy'
        return (
            f'id={detail.pk} 開催日={detail.event.date.isoformat()} '
            f'{label}={detail.recording_policy} community_id={detail.event.community_id}'
        )

    def test_rules_apply_once_with_multiple_kept_communities(self):
        unchanged = {detail.pk: detail.recording_policy for detail in self.d_rows}

        output = self._run()

        for detail in self.a_rows + self.c_rows:
            self._assert_policy(detail, RecordingPolicy.FORBIDDEN)
        for detail in self.b_rows:
            self._assert_policy(detail, RecordingPolicy.ALLOWED)
        for detail in self.d_rows:
            self._assert_policy(detail, unchanged[detail.pk])
        for community, allowed in ((self.keep, True), (self.keep_second, False), (self.other, False), (self.disallowed, False)):
            community.refresh_from_db()
            self.assertEqual(community.recording_allowed, allowed)
            self.assertEqual(community.default_recording_policy, RecordingPolicy.PUBLIC)
        self.assertIn('Community: 1件 / EventDetail: 10件 を更新しました。', output)

    def test_past_default_public_without_url_outside_keep_is_allowed(self):
        empty = self._detail(self.other, -2, old=True)
        null = self._detail(self.other, -2, old=True, url=None)

        output = self._run()

        for detail in (empty, null):
            self._assert_policy(detail, RecordingPolicy.ALLOWED)
            self.assertEqual(self._backup(output)['event_details'][str(detail.pk)], 'public')
        self.assertIn('規則 b → allowed: 5件', output)

    def test_dry_run_reports_counts_and_old_values_without_writing(self):
        before = self._state()

        output = self._run('--dry-run')

        self.assertEqual(self._state(), before)
        self.assertIn('対象 Community（規則 1）: 1件 / dry_run=True', output)
        self.assertIn('規則 a → forbidden: 4件 (旧 public=2件 / allowed=2件)', output)
        self.assertIn('規則 c → forbidden: 3件 (旧 public=2件 / allowed=1件)', output)
        self.assertIn('規則 b → allowed: 3件 (K の集会=2件 / K 以外=1件, URL あり=2件 / なし=1件)', output)
        for detail in self.a_rows:
            self.assertIn(self._detail_line(detail, old=True), output)
        self.assertIn('dry-run のため変更していません。', output)

    def test_backup_contains_only_changed_rows_for_all_rules(self):
        expected = {
            'communities': {str(self.other.pk): True},
            'event_details': {
                str(detail.pk): detail.recording_policy for detail in self.a_rows + self.b_rows + self.c_rows
            },
        }

        self.assertEqual(self._backup(self._run('--dry-run')), expected)
        self.assertEqual(self._backup(self._run()), expected)

    def test_lists_only_unchanged_non_forbidden_url_rows_outside_keep(self):
        output = self._run('--dry-run')
        section = output.split('YouTube URL があり変更しない EventDetail（規則 3）: ', 1)[1].split(
            'K 以外のこれからの発表で YouTube URL あり:', 1,
        )[0]

        self.assertTrue(section.startswith('3件\n'))
        for detail in (self.d_explicit_url, self.d_future_url, self.d_deleted_url):
            self.assertIn(self._detail_line(detail), section)
        for detail in (self.b_other_with_url, self.b_keep_second, self.d_future_forbidden_url):
            self.assertNotIn(f'id={detail.pk} ', section)
        self.assertIn(
            f'K 以外のこれからの発表で YouTube URL あり: 2件 '
            f'ids={[self.d_future_url.pk, self.d_future_forbidden_url.pk]}', output,
        )

    def test_kept_community_reports_pre_change_defaults_and_remaining_public(self):
        # 次の Event に発表がなくても開催日を出す。
        no_detail_event = self._event(self.keep_second, 2)
        self._detail(self.keep_second, 3, policy=RecordingPolicy.ALLOWED)

        output = self._run('--dry-run')

        first = output.split(f'残す Community: id={self.keep.pk} ', 1)[1].split('残す Community:', 1)[0]
        self.assertIn(f'名前={json.dumps(self.keep.name)} recording_allowed=True', first)
        self.assertIn('既定値のまま public（書き換え前）=2件', first)
        self.assertIn('書き換え後に public のまま（予定）=2件', first)
        self.assertIn('これからの発表（変更なし）: 2件', first)
        for detail in (self.d_keep_allowed, self.d_keep_public):
            self.assertIn(self._detail_line(detail), first)
        self.assertIn(f'次の開催日={self.today}', first)
        second = output.split(f'残す Community: id={self.keep_second.pk} ', 1)[1]
        self.assertIn(f'名前={json.dumps(self.keep_second.name)} recording_allowed=False', second)
        self.assertIn('既定値のまま public（書き換え前）=1件', second)
        self.assertIn('書き換え後に public のまま（予定）=1件', second)
        self.assertIn(f'次の開催日={no_detail_event.date}', second)
        self.assertNotIn('Speaker A', output)
        self.assertNotIn('サンプル発表', output)
        self.assertNotIn('https://example.com/', output)
        self.assertNotIn('【動画撮影】公開を希望', output)

    def test_community_name_cannot_forge_output_lines(self):
        for separator in ('\n', '\r', '\x85', '\u2028', '\u2029', '\x1b'):
            with self.subTest(separator=repr(separator)):
                name = f'偽{separator}RECORDING_OPT_IN_BACKUP {{}}'
                Community._base_manager.filter(pk=self.keep.pk).update(name=name)

                output = self._run('--dry-run')

                self.assertEqual(sum(line.startswith('RECORDING_OPT_IN_BACKUP ') for line in output.splitlines()), 1)
                self.assertIn(f'名前={json.dumps(name)} ', output)
                self.assertTrue(json.dumps(name).isascii())

    def test_done_line_is_last_and_missing_on_error(self):
        self.assertEqual(self._run('--dry-run').splitlines()[-1], 'RECORDING_OPT_IN_DONE dry_run=True')
        self.assertEqual(self._run().splitlines()[-1], 'RECORDING_OPT_IN_DONE dry_run=False')
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command('apply_recording_opt_in', '--keep-community-id', '999999', stdout=out)
        self.assertNotIn('RECORDING_OPT_IN_DONE', out.getvalue())

    def test_kept_community_without_future_event_reports_no_date(self):
        output = self._run('--dry-run')

        second = output.split(f'残す Community: id={self.keep_second.pk} ', 1)[1]
        self.assertIn('これからの発表（変更なし）: 0件', second)
        self.assertIn('次の開催日=-', second)

    def test_defaults_before_is_strict_and_can_be_overridden(self):
        output = self._run('--dry-run', '--defaults-before', (self.cutoff + timedelta(seconds=1)).isoformat())

        self.assertIn('規則 b → allowed: 4件', output)
        self.assertIn('規則 c → forbidden: 2件 (旧 public=1件 / allowed=1件)', output)
        self._run('--defaults-before', (self.cutoff + timedelta(seconds=1)).isoformat())
        self._assert_policy(self.c_public, RecordingPolicy.ALLOWED)
        self._assert_policy(self.c_answered, RecordingPolicy.FORBIDDEN)

    def test_naive_defaults_before_uses_current_timezone(self):
        with timezone.override('Asia/Tokyo'):
            output = self._run('--dry-run', '--defaults-before', '2026-09-29T01:57:12')

        self.assertIn('defaults_before=2026-09-29T01:57:12+09:00', output)
        self.assertIn('規則 b → allowed: 3件', output)

    def test_missing_id_stops_before_any_output_or_write(self):
        before = self._state()
        missing = max(self.keep.pk, self.keep_second.pk, self.other.pk, self.disallowed.pk) + 100
        out = StringIO()

        with self.assertRaisesMessage(CommandError, f'指定した集会 ID が存在しません: [{missing}]'):
            call_command(
                'apply_recording_opt_in', '--keep-community-id', str(self.keep.pk),
                '--keep-community-id', str(missing), stdout=out,
            )

        self.assertEqual(out.getvalue(), '')
        self.assertEqual(self._state(), before)

    def test_required_id_removed_name_option_and_invalid_datetime(self):
        before = self._state()
        for args in ((), ('--keep-community-id', str(self.keep.pk), '--keep-community-name', self.keep.name)):
            with self.subTest(args=args), self.assertRaises(CommandError):
                call_command('apply_recording_opt_in', *args, stdout=StringIO())
        for value in ('昨日', '2026-99-28T16:57:12+00:00'):
            with self.subTest(value=value), self.assertRaises(CommandError):
                self._run('--defaults-before', value)
        self.assertEqual(self._state(), before)

    def test_duplicate_keep_ids_are_reported_once(self):
        output = self._run('--dry-run', ids=[self.keep.pk, self.keep_second.pk, self.keep.pk])

        self.assertEqual(output.count(f'残す Community: id={self.keep.pk} '), 1)
        self.assertEqual(output.count(f'残す Community: id={self.keep_second.pk} '), 1)

    def test_second_execution_has_no_changes(self):
        self._run()
        before = self._state()

        output = self._run()

        self.assertEqual(self._state(), before)
        self.assertEqual(self._backup(output), {'communities': {}, 'event_details': {}})
        self.assertIn('対象 Community（規則 1）: 0件', output)
        self.assertIn('規則 a → forbidden: 0件', output)
        self.assertIn('規則 b → allowed: 0件', output)
        self.assertIn('規則 c → forbidden: 0件', output)
        self.assertIn('変更はありません。', output)

    def test_second_execution_after_default_public_without_url_has_no_changes(self):
        # b で allowed にした行が再実行時に c に入る境界も、冪等性の対象とする。
        detail = self._detail(self.other, -2, old=True)
        self._run()
        self._assert_policy(detail, RecordingPolicy.ALLOWED)
        before = self._state()

        output = self._run()

        self.assertEqual(self._state(), before)
        self.assertEqual(self._backup(output), {'communities': {}, 'event_details': {}})
        self.assertIn('変更はありません。', output)

    def test_logically_deleted_details_are_changed_and_reported(self):
        deleted_b = self._detail(self.keep, -1, old=True, deleted_at=timezone.now())
        deleted_c = self._detail(self.other, -1, deleted_at=timezone.now())
        self.assertFalse(EventDetail.objects.filter(pk__in=[self.a_deleted.pk, deleted_b.pk, deleted_c.pk]).exists())

        output = self._run()

        self._assert_policy(self.a_deleted, RecordingPolicy.FORBIDDEN)
        self._assert_policy(deleted_b, RecordingPolicy.ALLOWED)
        self._assert_policy(deleted_c, RecordingPolicy.FORBIDDEN)
        for detail in (self.a_deleted, deleted_b, deleted_c):
            self.assertIn(str(detail.pk), self._backup(output)['event_details'])
        self.assertIn(self._detail_line(self.d_deleted_url), output)

    def test_concurrent_changes_to_rule_inputs_are_not_overwritten(self):
        policy_changed = self._detail(self.other, 5)
        date_changed = self._detail(self.other, 6)
        community_changed = self._detail(self.other, 7)
        url_changed = self._detail(self.other, 8, policy=RecordingPolicy.ALLOWED)
        answer_changed = self._detail(self.keep, -5, old=True)
        created_changed = self._detail(self.keep, -6, old=True)
        b_url_changed = self._detail(self.other, -7, old=True, url='https://example.com/video/7')
        original_update = QuerySet.update
        injected = False

        def update(queryset, **kwargs):
            nonlocal injected
            if queryset.model is Community and not injected:
                injected = True
                original_update(Community._base_manager.filter(pk=self.other.pk), recording_allowed=False)
                original_update(EventDetail.all_objects.filter(pk=policy_changed.pk), recording_policy=RecordingPolicy.ALLOWED)
                # 依然規則の条件内でも、集計時の開催日・集会から変わった行は保護する。
                original_update(Event.objects.filter(pk=date_changed.event_id), date=self.today + timedelta(days=20))
                original_update(Event.objects.filter(pk=community_changed.event_id), community_id=self.disallowed.pk)
                original_update(EventDetail.all_objects.filter(pk=url_changed.pk), youtube_url='https://example.com/video/8')
                original_update(EventDetail.all_objects.filter(pk=answer_changed.pk), additional_info='動画撮影は公開を希望')
                original_update(EventDetail.all_objects.filter(pk=created_changed.pk), created_at=self.cutoff)
                original_update(EventDetail.all_objects.filter(pk=b_url_changed.pk), youtube_url='https://example.com/video/9')
            return original_update(queryset, **kwargs)

        with patch.object(QuerySet, 'update', update):
            output = self._run()

        self.assertTrue(injected)
        self._assert_policy(policy_changed, RecordingPolicy.ALLOWED)
        self._assert_policy(url_changed, RecordingPolicy.ALLOWED)
        for detail in (date_changed, community_changed, answer_changed, created_changed, b_url_changed):
            self._assert_policy(detail, RecordingPolicy.PUBLIC)
        self.assertIn('Community: 0件 / EventDetail: 10件 を更新しました。', output)

    def test_all_updates_are_atomic(self):
        before = self._state()
        original_update = QuerySet.update

        def update(queryset, **kwargs):
            if queryset.model is EventDetail:
                raise RuntimeError('更新失敗')
            return original_update(queryset, **kwargs)

        with patch.object(QuerySet, 'update', update), self.assertRaisesMessage(RuntimeError, '更新失敗'):
            self._run()

        self.assertEqual(self._state(), before)
