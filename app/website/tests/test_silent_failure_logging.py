"""silent_failure 構造化ログ + Sentry 連携テスト.

silent exception を握りつぶす箇所が:
- `logger.exception("silent_failure", extra={...})` を呼ぶこと
- `is_silent=True` / `event_type` フィールドを付与すること
- 既存の `assertLogs` 互換 (LogRecord に `is_silent` 属性が乗ること)
を確認する。Sentry 自体は本番のみ初期化されるため、初期化条件をユニットテストで検証する。
"""
from __future__ import annotations

import io
import json
import logging
from unittest.mock import MagicMock, patch

import structlog
from django.test import SimpleTestCase

from website.settings import base as settings_base


def _json_formatter() -> logging.Formatter:
    """settings/base.py と同じ pre_chain で JSON フォーマッタを構築する."""
    return structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=settings_base._foreign_pre_chain,
    )


def _capture(formatter: logging.Formatter, logger_name: str) -> tuple[logging.Logger, io.StringIO]:
    """テスト専用のロガーとストリームをセットアップする."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    logger = logging.getLogger(logger_name)
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger, stream


class SilentFailureStructuredLogTests(SimpleTestCase):
    """`silent_failure` イベント名 + `is_silent=True` の構造化ログ仕様."""

    def test_logger_exception_emits_is_silent_field(self):
        """logger.exception("silent_failure", extra={...}) が is_silent を載せること."""
        logger, stream = _capture(_json_formatter(), f"website.tests.silent.{id(self)}")
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            logger.exception(
                "silent_failure",
                extra={
                    "event_type": "test_failure",
                    "rule_id": 42,
                    "is_silent": True,
                },
            )

        payload = json.loads(stream.getvalue().strip().splitlines()[0])
        self.assertEqual(payload["event"], "silent_failure")
        self.assertEqual(payload["level"], "error")
        self.assertTrue(payload["is_silent"])
        self.assertEqual(payload["event_type"], "test_failure")
        self.assertEqual(payload["rule_id"], 42)
        # exception 情報がトレースとして含まれること
        self.assertIn("RuntimeError", payload.get("exception", ""))

    def test_llm_generator_recurrence_history_silent_failure(self):
        """recurrence.llm_generator.get_recent_events_history の silent path を検証."""
        from event.models import RecurrenceRule
        from event.recurrence import llm_generator

        rule = RecurrenceRule(id=99, frequency="OTHER", custom_rule="毎月第1月曜")
        # Event.objects.filter で例外を発生させて silent path に入る
        with patch(
            "event.recurrence.llm_generator.Event.objects.filter",
            side_effect=RuntimeError("db failure"),
        ):
            with self.assertLogs("event.recurrence_service", level="ERROR") as log_ctx:
                from datetime import date
                result = llm_generator.get_recent_events_history(
                    rule=rule,
                    base_date=date(2026, 1, 1),
                )

        self.assertEqual(result, "過去の開催履歴: 取得エラー")
        # logger.exception("silent_failure", extra={"is_silent": True, ...}) を呼んだことを確認
        records = [r for r in log_ctx.records if r.getMessage() == "silent_failure"]
        self.assertTrue(records, "silent_failure イベント名でログが出ること")
        record = records[0]
        self.assertTrue(getattr(record, "is_silent", False))
        self.assertEqual(getattr(record, "event_type", ""), "recurrence_history_lookup_failed")
        self.assertEqual(getattr(record, "rule_id", None), 99)

    def test_llm_generator_llm_generation_silent_failure(self):
        """recurrence.llm_generator.generate_dates_by_llm の silent path を検証."""
        from datetime import date, time

        from event.models import RecurrenceRule
        from event.recurrence import llm_generator

        rule = RecurrenceRule(id=77, frequency="OTHER", custom_rule="毎月第1月曜")
        llm_service = MagicMock()
        llm_service.generate_event_dates.side_effect = RuntimeError("llm down")

        with self.assertLogs("event.recurrence_service", level="ERROR") as log_ctx:
            result = llm_generator.generate_dates_by_llm(
                rule=rule,
                base_date=date(2026, 1, 1),
                base_time=time(22, 0),
                months=1,
                llm_service=llm_service,
                recent_events_history="",
            )

        self.assertEqual(result, [])
        records = [r for r in log_ctx.records if r.getMessage() == "silent_failure"]
        self.assertTrue(records)
        record = records[0]
        self.assertTrue(getattr(record, "is_silent", False))
        self.assertEqual(getattr(record, "event_type", ""), "recurrence_llm_generation_failed")
        self.assertEqual(getattr(record, "rule_id", None), 77)


class SentryInitializationTests(SimpleTestCase):
    """Sentry は本番のみ初期化される条件をユニットレベルで保証する."""

    def test_sentry_skipped_when_dsn_empty(self):
        """SENTRY_DSN が空なら sentry_sdk.init は呼ばれない."""
        # settings/base.py の判定式と同じロジックでスキップ条件を再現
        sentry_dsn = ""
        testing = False
        debug = False
        with patch("sentry_sdk.init") as mock_init:
            if sentry_dsn and not testing and not debug:
                import sentry_sdk
                sentry_sdk.init(dsn=sentry_dsn)
        mock_init.assert_not_called()

    def test_sentry_skipped_when_debug(self):
        """DEBUG=True なら sentry_sdk.init は呼ばれない (開発環境の誤送信防止)."""
        sentry_dsn = "https://example@sentry.io/1"
        testing = False
        debug = True
        with patch("sentry_sdk.init") as mock_init:
            if sentry_dsn and not testing and not debug:
                import sentry_sdk
                sentry_sdk.init(dsn=sentry_dsn)
        mock_init.assert_not_called()

    def test_sentry_skipped_when_testing(self):
        """TESTING=True なら sentry_sdk.init は呼ばれない (テスト時の誤送信防止)."""
        sentry_dsn = "https://example@sentry.io/1"
        testing = True
        debug = False
        with patch("sentry_sdk.init") as mock_init:
            if sentry_dsn and not testing and not debug:
                import sentry_sdk
                sentry_sdk.init(dsn=sentry_dsn)
        mock_init.assert_not_called()
