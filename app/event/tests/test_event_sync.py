from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from website.settings import REQUEST_TOKEN


class EventSyncTest(TestCase):
    @patch("event.views.sync.DatabaseToGoogleSync")
    def test_sync_calendar_events_respects_months_query(self, mock_sync_cls):
        """syncエンドポイントが months クエリを同期範囲へ渡すことを確認"""
        mock_sync = mock_sync_cls.return_value
        mock_sync.sync_all_communities.return_value = {
            "created": 0,
            "updated": 0,
            "deleted": 0,
            "errors": 0,
            "skipped": 0,
            "duplicate_prevented": 0,
        }

        response = self.client.get(
            reverse("event:sync_calendar_events") + "?months=6",
            HTTP_REQUEST_TOKEN=REQUEST_TOKEN,
        )

        self.assertEqual(response.status_code, 200)
        mock_sync.sync_all_communities.assert_called_once_with(months_ahead=6)

    @patch("event.views.sync.DatabaseToGoogleSync")
    def test_sync_calendar_events_rejects_invalid_months_query(self, mock_sync_cls):
        """syncエンドポイントが不正な months を拒否することを確認"""
        response = self.client.get(
            reverse("event:sync_calendar_events") + "?months=0",
            HTTP_REQUEST_TOKEN=REQUEST_TOKEN,
        )

        self.assertEqual(response.status_code, 400)
        mock_sync_cls.assert_not_called()
