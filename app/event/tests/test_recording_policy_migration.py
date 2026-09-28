"""recording_policy の DB 既定値と、既存の自由記述からの移行（event 0032）のテスト。"""

from importlib import import_module

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TransactionTestCase

from tests.factories import make_community, make_event

BACKFILL = import_module('event.migrations.0032_backfill_recording_policy_from_additional_info')
policy_from_additional_info = BACKFILL.policy_from_additional_info

LEGACY_LINE = '【動画撮影】YouTube公開 / Discord限定 / OK / NG'


def _info(recording_line):
    return f'【発表概要】\nVRChat の話\n\n【スライド公開】OK / NG\n\n{recording_line}'


class PolicyFromAdditionalInfoTest(SimpleTestCase):
    """自由記述の【動画撮影】の回答から撮影の扱いを決める純粋関数。"""

    def test_single_answer_maps_to_policy(self):
        """選択肢が 1 つだけ残っていれば対応する値になる。"""
        cases = {
            '【動画撮影】YouTube公開': 'public',
            '【動画撮影】Discord限定': 'allowed',
            '【動画撮影】OK': 'allowed',
            '【動画撮影】NG': 'forbidden',
        }
        for line, expected in cases.items():
            with self.subTest(line=line):
                self.assertEqual(policy_from_additional_info(_info(line)), expected)

    def test_notation_variants(self):
        """全角・小文字・空白の表記ゆれを吸収する。"""
        cases = {
            '【動画撮影】ＮＧ': 'forbidden',
            '【動画撮影】ng です': 'forbidden',
            '【動画撮影】 ｏｋ': 'allowed',
            '【動画撮影】youtube 公開': 'public',
            '【 動画撮影 】NG': 'forbidden',
        }
        for line, expected in cases.items():
            with self.subTest(line=line):
                self.assertEqual(policy_from_additional_info(_info(line)), expected)

    def test_unanswered_or_ambiguous_falls_back_to_allowed(self):
        """未回答（選択肢が 2 つ以上残る）・判別できない回答は安全側の allowed。"""
        for line in (LEGACY_LINE, '【動画撮影】OK / NG', '【動画撮影】', '【動画撮影】おまかせします'):
            with self.subTest(line=line):
                self.assertEqual(policy_from_additional_info(_info(line)), 'allowed')

    def test_answer_ends_at_next_heading(self):
        """回答は次の【まで。後ろの項目の OK / NG を拾わない。"""
        text = '【動画撮影】NG【スライド公開】OK'

        self.assertEqual(policy_from_additional_info(text), 'forbidden')

    def test_answer_on_next_line(self):
        """見出しの次の行に書かれた回答も拾う。"""
        text = '【動画撮影】\nNG\n\n【対象者】初心者'

        self.assertEqual(policy_from_additional_info(text), 'forbidden')

    def test_does_not_match_inside_words(self):
        """英単語の途中の ok / ng には反応しない。"""
        self.assertEqual(policy_from_additional_info('【動画撮影】booking 次第'), 'allowed')

    def test_no_recording_line_returns_none(self):
        """【動画撮影】の行が無ければ変更しない（None）。"""
        for text in ('', None, '【発表概要】\n\n【スライド公開】NG'):
            with self.subTest(text=text):
                self.assertIsNone(policy_from_additional_info(text))


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
