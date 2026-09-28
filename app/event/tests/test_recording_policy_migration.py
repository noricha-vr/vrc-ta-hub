"""recording_policy の DB 既定値と、既存の自由記述からの移行（event 0032）のテスト。"""

from io import StringIO

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TestCase, TransactionTestCase

from event.models import EventDetail
from event.recording_policy_answers import plan_policy_changes, policy_from_additional_info
from tests.factories import make_community, make_event, make_event_detail

LEGACY_LINE = '【動画撮影】YouTube公開 / Discord限定 / OK / NG'


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

    def _run(self, *args):
        out = StringIO()
        call_command('backfill_recording_policy', *args, stdout=out)
        return out.getvalue()

    def _policies(self):
        return dict(EventDetail.all_objects.values_list('theme', 'recording_policy'))

    def test_dry_run_reports_without_changing(self):
        """--dry-run は件数と変更内容を出すだけで書き換えない。"""
        output = self._run('--dry-run')

        self.assertIn('対象 EventDetail: 3件', output)
        self.assertIn(f'public -> forbidden: 1件 ids=[{self.refused.pk}]', output)
        self.assertIn(f'public -> allowed: 1件 ids=[{self.unanswered.pk}]', output)
        self.assertEqual(set(self._policies().values()), {'public', 'allowed'})
        self.assertEqual(self._policies()['拒否'], 'public')

    def test_applies_only_to_public_rows_and_is_idempotent(self):
        """public の発表だけ当て直し、2 回目は何も変えない。"""
        self._run()

        self.assertEqual(self._policies(), {
            '拒否': 'forbidden', '未回答': 'allowed', '公開': 'public', '選択済み': 'allowed',
        })
        self.assertIn('変更はありません', self._run())
