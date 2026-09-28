"""recording_policy の DB 既定値と、既存の自由記述からの移行（event 0032）のテスト。"""

from datetime import timedelta
from importlib import import_module
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.utils import timezone

from event.models import EventDetail
from event.recording_policy_answers import plan_policy_changes, policy_from_additional_info
from tests.factories import make_community, make_event, make_event_detail

LEGACY_LINE = '【動画撮影】YouTube公開 / Discord限定 / OK / NG'
MIGRATION_0032 = import_module('event.migrations.0032_backfill_recording_policy_from_additional_info')

# migration に固定した判定とアプリの判定が同じ結果になることを確かめる入力の表
PARITY_INPUTS = (
    None, '', '行なし', '【発表概要】\n\n【スライド公開】NG',
    LEGACY_LINE, '【動画撮影】YouTube公開 / OK / NG', LEGACY_LINE + ' 撮影不可',
    '【動画撮影】NG', '【動画撮影】ＮＧ', '【動画撮影】撮影不可', '【動画撮影】撮影しないでください',
    '【動画撮影】撮影NG、YouTube公開もNG', '【動画撮影】YouTube公開 / NG', '【動画撮影】ﾀﾞﾒです',
    '【動画撮影】×', '【動画撮影】No', '【動画撮影】YouTube公開', '【動画撮影】Discord限定',
    '【動画撮影】OK', '【動画撮影】', '【動画撮影】おまかせします', '【動画撮影】YouTube公開 / OK',
    '【動画撮影】\nNG\n\n【対象者】初心者', '【動画撮影】YouTube公開【スライド公開】NG',
    '【動画撮影】booking 次第、nothing',
    LEGACY_LINE + '\n撮影不可\n\n【対象者】初心者', LEGACY_LINE + '\n\n【対象者】初心者',
    '【動画撮影】\nNG', '【動画撮影】YouTube公開\nやっぱり撮影しないでください',
    '【動画撮影】撮影しません', '【動画撮影】撮らないでください', '【動画撮影】撮影は遠慮します',
    '【動画撮影】撮影はお控えください', '【動画撮影】ご遠慮ください', '【動画撮影】やめてください',
    '【動画撮影】撮影不要', LEGACY_LINE + '\n撮らないで',
)


def _info(recording_line):
    return f'【発表概要】\nVRChat の話\n\n【スライド公開】OK / NG\n\n{recording_line}'


class PolicyFromAdditionalInfoTest(SimpleTestCase):
    """自由記述の【動画撮影】の回答から撮影の扱いを決める純粋関数。"""

    def _assert_cases(self, cases):
        for line, expected in cases.items():
            with self.subTest(line=line):
                self.assertEqual(policy_from_additional_info(_info(line)), expected)

    def test_template_left_as_is_is_allowed(self):
        """選択肢が 3 つ以上そのまま残っている（未回答）なら allowed。"""
        self._assert_cases({
            LEGACY_LINE: 'allowed',
            '【動画撮影】YouTube公開 / OK / NG': 'allowed',
        })

    def test_refusal_added_to_unanswered_template_is_forbidden(self):
        """テンプレの並びを残したままでも、書き足した拒否の語があれば forbidden。"""
        self._assert_cases({
            LEGACY_LINE + ' 撮影不可': 'forbidden',
            LEGACY_LINE + '（撮影しないでください）': 'forbidden',
            LEGACY_LINE + ' → NG': 'forbidden',
            '【動画撮影】YouTube公開 / OK / NG ×': 'forbidden',
        })

    def test_refusal_words_are_forbidden(self):
        """拒否を表す語があれば forbidden（選択肢が 1〜2 個でも優先）。"""
        self._assert_cases({
            '【動画撮影】NG': 'forbidden',
            '【動画撮影】ＮＧ': 'forbidden',
            '【動画撮影】ng です': 'forbidden',
            '【動画撮影】撮影不可': 'forbidden',
            '【動画撮影】撮影しないでください': 'forbidden',
            '【動画撮影】撮影NG、YouTube公開もNG': 'forbidden',
            '【動画撮影】YouTube公開 / NG': 'forbidden',
            '【動画撮影】OK / NG': 'forbidden',
            '【動画撮影】禁止': 'forbidden',
            '【動画撮影】ﾀﾞﾒです': 'forbidden',
            '【動画撮影】だめ': 'forbidden',
            '【動画撮影】お断りします': 'forbidden',
            '【動画撮影】×': 'forbidden',
            '【動画撮影】✕': 'forbidden',
            '【動画撮影】No': 'forbidden',
            '【動画撮影】撮影しません': 'forbidden',
            '【動画撮影】撮らないでください': 'forbidden',
            '【動画撮影】撮らないで': 'forbidden',
            '【動画撮影】撮影は遠慮します': 'forbidden',
            '【動画撮影】ご遠慮ください': 'forbidden',
            '【動画撮影】撮影はお控えください': 'forbidden',
            '【動画撮影】控えてください': 'forbidden',
            '【動画撮影】やめてください': 'forbidden',
            '【動画撮影】撮影不要': 'forbidden',
            '【 動画撮影 】NG': 'forbidden',
        })

    def test_single_option_maps_to_policy(self):
        """拒否の語が無く選択肢が 1 つだけなら対応する値になる。"""
        self._assert_cases({
            '【動画撮影】YouTube公開': 'public',
            '【動画撮影】youtube 公開': 'public',
            '【動画撮影】Discord限定': 'allowed',
            '【動画撮影】OK': 'allowed',
            '【動画撮影】 ｏｋ': 'allowed',
        })

    def test_other_answers_fall_back_to_allowed(self):
        """空・判別できない・選択肢が 2 つ残る回答は安全側の allowed。"""
        self._assert_cases({
            '【動画撮影】': 'allowed',
            '【動画撮影】おまかせします': 'allowed',
            '【動画撮影】YouTube公開 / OK': 'allowed',
        })

    def test_answer_ends_at_next_heading(self):
        """回答は次の【まで。後ろの項目の NG を拾わない。"""
        self.assertEqual(policy_from_additional_info('【動画撮影】YouTube公開【スライド公開】NG'), 'public')

    def test_answer_on_next_line(self):
        """見出しの次の行に書かれた回答も拾う。"""
        self.assertEqual(policy_from_additional_info('【動画撮影】\nNG\n\n【対象者】初心者'), 'forbidden')
        self.assertEqual(policy_from_additional_info('【動画撮影】\nNG'), 'forbidden')

    def test_answer_covers_all_lines_until_next_heading(self):
        """回答は次の【までの全行。後の行に書き足した拒否も拾う。"""
        cases = {
            LEGACY_LINE + '\n撮影不可\n\n【対象者】初心者': 'forbidden',
            LEGACY_LINE + '\n\n【対象者】初心者': 'allowed',
            '【動画撮影】YouTube公開\nやっぱり撮影しないでください': 'forbidden',
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(policy_from_additional_info(text), expected)

    def test_does_not_match_inside_words(self):
        """英単語の途中の ok / ng / no には反応しない。"""
        self.assertEqual(policy_from_additional_info('【動画撮影】booking 次第、nothing'), 'allowed')

    def test_no_recording_line_returns_none(self):
        """【動画撮影】の行が無ければ変更しない（None）。"""
        for text in ('', None, '【発表概要】\n\n【スライド公開】NG'):
            with self.subTest(text=text):
                self.assertIsNone(policy_from_additional_info(text))

    def test_plan_groups_changes_and_skips_public(self):
        """変更計画は public 以外だけを扱いごとにまとめる。"""
        rows = [
            (1, _info('【動画撮影】NG')),
            (2, _info(LEGACY_LINE)),
            (3, _info('【動画撮影】YouTube公開')),
            (4, '行なし'),
            (5, _info('【動画撮影】不可')),
        ]

        self.assertEqual(plan_policy_changes(rows), {'forbidden': [1, 5], 'allowed': [2]})


class MigrationAndAppJudgementParityTest(SimpleTestCase):
    """migration 0032 に固定した判定とアプリの判定が、同じ入力で同じ結果になる。"""

    def test_same_results_for_input_table(self):
        for text in PARITY_INPUTS:
            with self.subTest(text=text):
                self.assertEqual(
                    MIGRATION_0032.policy_from_additional_info(text),
                    policy_from_additional_info(text),
                )

    def test_migration_does_not_import_app_module(self):
        """0032 はアプリの判定モジュールを import していない（写しで固定）。"""
        self.assertIsNot(MIGRATION_0032.policy_from_additional_info, policy_from_additional_info)
        self.assertEqual(
            MIGRATION_0032.policy_from_additional_info.__module__,
            MIGRATION_0032.__name__,
        )


class BackfillRecordingPolicyMigrationTest(TransactionTestCase):
    """event 0032 が既存の発表の recording_policy を回答どおりに埋める。"""

    migrate_from = [('event', '0031_eventdetail_recording_policy')]
    migrate_to = [('event', '0032_backfill_recording_policy_from_additional_info')]

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        self.OldEventDetail = old_apps.get_model('event', 'EventDetail')
        self.event = make_event(make_community(name='移行の集会'))

    def tearDown(self):
        MigrationExecutor(connection).migrate(self.migrate_to)
        super().tearDown()

    def _make(self, additional_info, **extra):
        return self.OldEventDetail.objects.create(
            event_id=self.event.pk, theme='発表', speaker='発表者', additional_info=additional_info, **extra,
        ).pk

    def test_backfills_from_answers(self):
        """回答に応じて埋まり、行が無い発表は public のまま。"""
        pks = {
            'forbidden': self._make(_info('【動画撮影】NG')),
            'allowed': self._make(_info(LEGACY_LINE)),
            'public_answer': self._make(_info('【動画撮影】YouTube公開')),
            'no_line': self._make('【発表概要】\n\n【スライド公開】OK'),
        }
        deleted_pk = self._make(_info('【動画撮影】ＮＧ'), deleted_at='2026-01-01T00:00:00Z')

        MigrationExecutor(connection).migrate(self.migrate_to)

        from event.models import EventDetail
        policy = dict(EventDetail.all_objects.values_list('pk', 'recording_policy'))
        self.assertEqual(policy[pks['forbidden']], 'forbidden')
        self.assertEqual(policy[pks['allowed']], 'allowed')
        self.assertEqual(policy[pks['public_answer']], 'public')
        self.assertEqual(policy[pks['no_line']], 'public')
        self.assertEqual(policy[deleted_pk], 'forbidden')


class RecordingPolicyDbDefaultTest(TransactionTestCase):
    """migration 適用後に動く旧リビジョン（列を知らないコード）の INSERT が通る。"""

    def test_old_model_insert_gets_db_default(self):
        """recording_policy を知らない旧モデルで作っても、DB 既定値で public になる。"""
        old_state = MigrationExecutor(connection).loader.project_state(
            [('event', '0030_eventdetail_cached_transcript_and_more')],
        )
        OldEventDetail = old_state.apps.get_model('event', 'EventDetail')
        self.assertNotIn('recording_policy', [f.name for f in OldEventDetail._meta.get_fields()])
        event = make_event(make_community(name='旧リビジョンの集会'))

        pk = OldEventDetail.objects.create(event_id=event.pk, theme='旧', speaker='旧').pk

        from event.models import EventDetail
        self.assertEqual(EventDetail.all_objects.get(pk=pk).recording_policy, 'public')


class BackfillRecordingPolicyCommandTest(TestCase):
    """デプロイ後に当て直す management command。"""

    def setUp(self):
        self.event = make_event(make_community(name='再実行の集会'))
        self.refused = make_event_detail(self.event, theme='拒否', additional_info=_info('【動画撮影】撮影不可'))
        self.unanswered = make_event_detail(self.event, theme='未回答', additional_info=_info(LEGACY_LINE))
        self.public_answer = make_event_detail(
            self.event, theme='公開', additional_info=_info('【動画撮影】YouTube公開'),
        )
        # 既に選び直された発表（public 以外）は回答と食い違っても触らない
        self.chosen = make_event_detail(
            self.event, theme='選択済み', additional_info=_info('【動画撮影】NG'),
            recording_policy=EventDetail.RecordingPolicy.ALLOWED,
        )
        # --since より前（0032 が判定済み、または新しい画面で「公開」を選んだ）の発表
        self.before_since = make_event_detail(
            self.event, theme='以前', additional_info=_info('【動画撮影】NG'),
        )
        self.since = timezone.now() - timedelta(hours=1)
        EventDetail.all_objects.filter(pk=self.before_since.pk).update(
            created_at=self.since - timedelta(minutes=1),
        )

    def _run(self, *args, since=None):
        out = StringIO()
        since = since or self.since.isoformat()
        call_command('backfill_recording_policy', '--since', since, *args, stdout=out)
        return out.getvalue()

    def _policies(self):
        return dict(EventDetail.all_objects.values_list('theme', 'recording_policy'))

    def test_dry_run_reports_without_changing(self):
        """--dry-run は件数と変更内容を出すだけで書き換えない。"""
        output = self._run('--dry-run')

        self.assertIn('対象 EventDetail: 3件', output)
        self.assertIn(f'public -> forbidden: 1件 ids=[{self.refused.pk}]', output)
        self.assertIn(f'public -> allowed: 1件 ids=[{self.unanswered.pk}]', output)
        self.assertEqual(self._policies()['拒否'], 'public')
        self.assertEqual(self._policies()['未回答'], 'public')

    def test_applies_only_to_public_rows_since_and_is_idempotent(self):
        """--since 以降の public の発表だけ当て直し、2 回目は何も変えない。"""
        self._run()

        self.assertEqual(self._policies(), {
            '拒否': 'forbidden', '未回答': 'allowed', '公開': 'public', '選択済み': 'allowed', '以前': 'public',
        })
        self.assertIn('変更はありません', self._run())

    def test_until_excludes_later_rows(self):
        """--until より後に作られた発表は触らない。"""
        until = timezone.now() + timedelta(minutes=30)
        EventDetail.all_objects.filter(pk=self.refused.pk).update(created_at=until + timedelta(minutes=1))

        self._run('--until', until.isoformat())

        self.assertEqual(self._policies()['拒否'], 'public')
        self.assertEqual(self._policies()['未回答'], 'allowed')

    def test_since_is_required_and_validated(self):
        """--since は必須で、ISO 8601 でなければエラー。"""
        with self.assertRaises(CommandError):
            call_command('backfill_recording_policy', stdout=StringIO())
        with self.assertRaises(CommandError):
            self._run(since='昨日')
        self.assertEqual(self._policies()['拒否'], 'public')
