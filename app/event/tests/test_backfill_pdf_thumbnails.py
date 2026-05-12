from datetime import date, time
from io import StringIO
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase

from community.models import Community
from event.models import Event, EventDetail


class BackfillEventDetailPdfThumbnailsCommandTest(TestCase):
    """EventDetail PDFサムネイル再生成コマンドのテスト."""

    def setUp(self):
        self.community = Community.objects.create(name="Test Community")
        self.event = Event.objects.create(
            community=self.community,
            date=date(2025, 1, 1),
            start_time=time(22, 0),
            duration=60,
            weekday="Wed",
        )
        self.detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="pending",
            theme="Test Theme",
            speaker="Test Speaker",
        )
        self.detail.slide_file.save("test.pdf", ContentFile(b"%PDF-1.4\n%%EOF"), save=True)
        self.detail.thumbnail_image.save("existing.jpg", ContentFile(b"old"), save=True)

    @patch("event.management.commands.backfill_event_detail_pdf_thumbnails.ensure_pdf_thumbnail")
    def test_dry_run_does_not_generate_thumbnail(self, mock_ensure_pdf_thumbnail):
        """dry-runでは対象を表示するだけで生成しない."""
        stdout = StringIO()

        call_command("backfill_event_detail_pdf_thumbnails", "--dry-run", "--force", stdout=stdout)

        self.assertIn(f"DRY-RUN id={self.detail.pk}", stdout.getvalue())
        mock_ensure_pdf_thumbnail.assert_not_called()

    @patch("event.management.commands.backfill_event_detail_pdf_thumbnails.sync_slide_share_queue_image")
    @patch("event.management.commands.backfill_event_detail_pdf_thumbnails.ensure_pdf_thumbnail", return_value=True)
    def test_force_overwrites_existing_thumbnail(self, mock_ensure_pdf_thumbnail, mock_sync_image):
        """force指定では既存サムネイルありのPDFも上書き対象にする."""
        stdout = StringIO()

        call_command("backfill_event_detail_pdf_thumbnails", "--force", stdout=stdout)

        mock_ensure_pdf_thumbnail.assert_called_once()
        event_detail = mock_ensure_pdf_thumbnail.call_args.args[0]
        self.assertEqual(event_detail.pk, self.detail.pk)
        self.assertEqual(mock_ensure_pdf_thumbnail.call_args.kwargs, {"save": True, "overwrite": True})
        mock_sync_image.assert_called_once_with(event_detail)
        self.assertIn("updated=1", stdout.getvalue())
