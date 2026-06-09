from datetime import date, time
from unittest.mock import MagicMock, patch

from django.test import TestCase

from event.models import RecurrenceRule
from event.recurrence_service import RecurrenceService


class RecurrenceServiceLoggingTest(TestCase):
    def test_recent_events_history_error_uses_logger(self):
        service = RecurrenceService.__new__(RecurrenceService)
        rule = RecurrenceRule(id=1, frequency="OTHER", custom_rule="毎月第1月曜")

        with patch(
            "event.recurrence_service.Event.objects.filter",
            side_effect=RuntimeError("database failure"),
        ):
            with self.assertLogs("event.recurrence_service", level="ERROR") as log_context:
                result = service._get_recent_events_history(
                    rule=rule,
                    base_date=date(2026, 1, 1),
                )

        self.assertEqual(result, "過去の開催履歴: 取得エラー")
        # silent_failure 形式の構造化ログに移行 (詳細は docs/sentry.md)
        self.assertIn("silent_failure", "\n".join(log_context.output))

    def test_llm_date_generation_error_uses_logger(self):
        service = RecurrenceService.__new__(RecurrenceService)
        # LLM サービス取得後の呼び出しで例外を発生させる
        llm_service = MagicMock()
        llm_service.generate_event_dates.side_effect = RuntimeError("llm failure")
        service._get_llm_service = MagicMock(return_value=llm_service)
        # DBアクセスを避けるため履歴取得もモック
        service._get_recent_events_history = MagicMock(return_value="")
        rule = RecurrenceRule(frequency="OTHER", custom_rule="毎月第1月曜")

        with self.assertLogs("event.recurrence_service", level="ERROR") as log_context:
            result = service._generate_dates_by_llm(
                rule=rule,
                base_date=date(2026, 1, 1),
                base_time=time(22, 0),
                months=1,
            )

        self.assertEqual(result, [])
        # silent_failure 形式の構造化ログに移行 (詳細は docs/sentry.md)
        self.assertIn("silent_failure", "\n".join(log_context.output))
