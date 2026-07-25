"""sync_calendar 管理コマンドの exit code 契約テスト。

cron / Cloud Run Job から失敗を検知できるよう、同期 view が非200を返したら
CommandError（exit code 1）で終了することを保証する。
"""
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.http import HttpResponse
from django.test import TestCase


class SyncCalendarCommandTest(TestCase):
    """sync_calendar コマンドのユニットテスト。"""

    def _call(self):
        out = StringIO()
        err = StringIO()
        call_command("sync_calendar", stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    @patch("event.management.commands.sync_calendar.sync_calendar_events")
    def test_success_writes_body_to_stdout(self, mock_sync):
        """200 応答なら成功メッセージと body を stdout に出す。"""
        mock_sync.return_value = HttpResponse("Sync OK", status=200)

        out, _ = self._call()

        self.assertIn("Sync completed. Status: 200", out)
        self.assertIn("Sync OK", out)

    @patch("event.management.commands.sync_calendar.sync_calendar_events")
    def test_non_200_raises_command_error(self, mock_sync):
        """非200応答なら CommandError で異常終了する（exit code 1）。"""
        mock_sync.return_value = HttpResponse("Calendar API error", status=500)

        with self.assertRaises(CommandError) as ctx:
            self._call()

        self.assertIn("Sync failed. Status: 500", str(ctx.exception))
        self.assertIn("Calendar API error", str(ctx.exception))

    @patch("event.management.commands.sync_calendar.sync_calendar_events")
    def test_forbidden_response_raises_command_error(self, mock_sync):
        """認証失敗(403)でも成功扱いしない。"""
        mock_sync.return_value = HttpResponse("Invalid token", status=403)

        with self.assertRaises(CommandError):
            self._call()
