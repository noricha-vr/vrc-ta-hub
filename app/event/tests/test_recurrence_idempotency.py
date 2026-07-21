"""generate_recurring_events コマンドの冪等性テスト

`RecurrenceRule.last_generated_date` を導入し、複数回連続実行しても Event が
重複生成されないことと、LLM 失敗からのリカバリが効くことを検証する。
"""
from datetime import time, timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, tag
from django.utils import timezone

from community.models import Community
from event.models import Event, RecurrenceRule
from event.tests.tweet_generation import TweetGenerationPatchMixin
from user_account.models import CustomUser


@tag('offline_external_api')
class RecurrenceIdempotencyTest(TweetGenerationPatchMixin, TestCase):
    """generate_recurring_events を二度実行しても重複しないことを保証する。"""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            user_name='idempotency_user',
            email='idempotency@example.com',
            password='testpass',
        )
        self.community = Community.objects.create(
            name='冪等性テストコミュニティ',
            description='冪等性テスト用',
            weekdays=['Mon'],
            start_time=time(21, 0),
            duration=60,
            status='approved',
        )
        self.rule = RecurrenceRule.objects.create(
            community=self.community,
            frequency='WEEKLY',
            interval=1,
        )
        self.master_event = Event.objects.create(
            community=self.community,
            date=timezone.now().date() - timedelta(days=7),
            start_time=time(21, 0),
            duration=60,
            weekday='MON',
            is_recurring_master=True,
            recurrence_rule=self.rule,
        )

    def _run_command(self, *extra_args):
        out = StringIO()
        call_command('generate_recurring_events', '--months=1', *extra_args, stdout=out)
        return out.getvalue()

    def _instance_dates(self):
        return set(
            Event.objects.filter(recurring_master=self.master_event).values_list('date', flat=True)
        )

    def test_double_run_does_not_duplicate_events(self):
        """2 回連続実行しても同じ日付の Event が重複しないこと

        WEEKLY ルールの base_date は前回実行の last_instance + 1 になるため、
        2 回目は 1 回目より先の日付が 1 件追加されることがある（自然な挙動）。
        したがって「件数が変わらない」ではなく「同じ (community, date, start_time)
        の Event が 2 件以上にならない」ことを冪等性の定義とする。
        """
        self._run_command()
        first_dates = self._instance_dates()
        self.assertGreater(len(first_dates), 0)

        # 2 回目: 既存日付に対しては重複生成されないはず
        self._run_command()
        second_dates = self._instance_dates()

        # 1 回目で作った日付は全て 2 回目でも残っており、重複もない
        self.assertTrue(first_dates.issubset(second_dates))

        # (community, date, start_time) のユニーク重複が無いこと
        from django.db.models import Count
        duplicates = (
            Event.objects.filter(community=self.community)
            .values('date', 'start_time')
            .annotate(c=Count('id'))
            .filter(c__gt=1)
        )
        self.assertEqual(list(duplicates), [], '同一日付・時刻の Event が重複している')

    def test_last_generated_date_is_updated(self):
        """初回実行で last_generated_date が更新される"""
        self.assertIsNone(self.rule.last_generated_date)

        self._run_command()

        self.rule.refresh_from_db()
        self.assertIsNotNone(self.rule.last_generated_date)
        # 生成したイベントの最大日付と一致するはず
        max_date = max(self._instance_dates())
        self.assertEqual(self.rule.last_generated_date, max_date)

    def test_last_generated_date_only_advances_forward(self):
        """2 回目の実行で last_generated_date が後退しないこと"""
        self._run_command()
        self.rule.refresh_from_db()
        first_last = self.rule.last_generated_date
        self.assertIsNotNone(first_last)

        # 2 回目: 新規作成は 0 件のはずだが、last_generated_date は維持される
        self._run_command()
        self.rule.refresh_from_db()
        self.assertIsNotNone(self.rule.last_generated_date)
        self.assertGreaterEqual(self.rule.last_generated_date, first_last)

    def test_existing_events_are_skipped(self):
        """既に存在する日付には新たに作らない（重複防止チェックが効いている）"""
        today = timezone.now().date()
        # 先に手動でその週分の Event を作っておく
        target_date = today + timedelta(days=(0 - today.weekday()) % 7 + 7)
        Event.objects.create(
            community=self.community,
            date=target_date,
            start_time=time(21, 0),
            duration=60,
            weekday='MON',
            recurring_master=self.master_event,
        )
        pre_count = Event.objects.filter(
            community=self.community, date=target_date, start_time=time(21, 0)
        ).count()
        self.assertEqual(pre_count, 1)

        self._run_command()

        # 同じ日付の Event が 1 件のまま（重複していない）
        post_count = Event.objects.filter(
            community=self.community, date=target_date, start_time=time(21, 0)
        ).count()
        self.assertEqual(post_count, 1)

    def test_recovery_after_llm_failure(self):
        """LLM 失敗で空配列が返ったあと、再実行で残りを生成できる

        OTHER frequency の rule で LLM が一時的に失敗 → 次回実行で復帰、
        という流れを再現する。1 回目は generate_dates が [] を返すため
        last_generated_date は更新されず、Event も作られない。
        2 回目は通常通り日付が返り、Event が作成される。
        """
        # OTHER ルールに切り替え
        self.rule.frequency = 'OTHER'
        self.rule.custom_rule = '毎週月曜日'
        self.rule.save(update_fields=['frequency', 'custom_rule'])

        with patch(
            'event.recurrence_service.RecurrenceService.generate_dates',
            return_value=[],
        ):
            self._run_command()

        # LLM 失敗時: 新規 Event は作られず、last_generated_date も None のまま
        self.rule.refresh_from_db()
        self.assertEqual(
            Event.objects.filter(recurring_master=self.master_event).count(),
            0,
        )
        self.assertIsNone(self.rule.last_generated_date)

        # 2 回目: LLM が復活したと仮定し、実際の日付を返す
        today = timezone.now().date()
        recovered_dates = [today + timedelta(days=i * 7) for i in range(1, 4)]
        with patch(
            'event.recurrence_service.RecurrenceService.generate_dates',
            return_value=recovered_dates,
        ):
            self._run_command()

        # 新規作成され、last_generated_date が更新される
        created = Event.objects.filter(recurring_master=self.master_event)
        self.assertGreater(created.count(), 0)
        self.rule.refresh_from_db()
        self.assertIsNotNone(self.rule.last_generated_date)
        self.assertEqual(self.rule.last_generated_date, max(recovered_dates))

    def test_dry_run_does_not_update_last_generated_date(self):
        """--dry-run では last_generated_date を更新しないこと"""
        self.assertIsNone(self.rule.last_generated_date)
        self._run_command('--dry-run')
        self.rule.refresh_from_db()
        self.assertIsNone(self.rule.last_generated_date)
