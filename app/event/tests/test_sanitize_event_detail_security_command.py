from unittest.mock import MagicMock

from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase

from community.models import Community
from event.management.commands.sanitize_event_detail_security import _is_pdf_magic_bytes
from event.models import Event, EventDetail


class TestSanitizeEventDetailSecurityCommand(TestCase):
    def test_pdf_magic_bytes_logs_file_errors(self):
        """slide_fileの読み取りとclose失敗はログ出力してFalseを返す"""
        slide_file = MagicMock()
        slide_file.name = "broken.pdf"
        slide_file.open.side_effect = OSError("open failed")
        slide_file.close.side_effect = OSError("close failed")

        with self.assertLogs(
            "event.management.commands.sanitize_event_detail_security",
            level="ERROR",
        ) as log_ctx:
            result = _is_pdf_magic_bytes(slide_file)

        self.assertFalse(result)
        self.assertIn("slide_fileのPDFマジックバイト判定に失敗しました", log_ctx.output[0])
        self.assertIn("slide_fileのクローズに失敗しました", log_ctx.output[1])

    def test_detach_invalid_slide_file_and_sanitize_contents(self):
        community = Community.objects.create(name="テスト集会")
        event = Event.objects.create(date="2024-05-24", community=community)
        event_detail = EventDetail.objects.create(
            event=event,
            theme="テストテーマ",
            speaker="テストスピーカー",
        )

        event_detail.contents = (
            "before\n"
            '<iframe src="https://data.vrc-ta-hub.com/slide/xssexploit.pdf" width="500" height="500"></iframe>\n'
            "after\n"
        )
        event_detail.save(update_fields=["contents"])

        # 既に不正ファイルが保存されている前提を作る（FileField.saveはvalidatorを通らない）
        event_detail.slide_file.save(
            "evil.pdf",
            ContentFile(b"<html><script>alert(1)</script></html>"),
            save=True,
        )

        call_command(
            "sanitize_event_detail_security",
            ids=str(event_detail.id),
            apply=True,
            delete_files=True,
            verbosity=0,
        )

        event_detail.refresh_from_db()
        self.assertNotIn("<iframe", event_detail.contents)
        self.assertFalse(bool(event_detail.slide_file))
