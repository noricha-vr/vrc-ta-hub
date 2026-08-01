from datetime import datetime, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from community.models import Community
from event.models import Event
from event.sync_to_google import DatabaseToGoogleSync
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


class DatabaseToGoogleSyncSkipTest(TestCase):
    """日付移動後の DB→Google 同期のスキップ判定"""

    @patch('event.sync_to_google.GoogleCalendarService')
    def test_database_sync_skips_already_updated_date_and_id(
        self,
        calendar_service_class,
    ):
        """日付移動後に日時とIDが一致すればGoogleを再更新しない"""
        community = Community.objects.create(
            name="個人開発集会",
            status="approved",
        )
        future_date = timezone.now().date() + timedelta(days=30)
        event = Event.objects.create(
            community=community,
            date=future_date,
            start_time="21:00:00",
            duration=60,
            weekday=future_date.strftime("%a"),
            google_calendar_event_id="event1_id",
        )
        event.refresh_from_db()
        start_at = timezone.make_aware(
            datetime.combine(event.date, event.start_time)
        )
        calendar_service_class.return_value.list_events.return_value = [{
            'id': event.google_calendar_event_id,
            'summary': community.name,
            'start': {'dateTime': start_at.isoformat()},
            'end': {
                'dateTime': (
                    start_at + timedelta(minutes=event.duration)
                ).isoformat(),
            },
        }]

        stats = DatabaseToGoogleSync().sync_all_communities(months_ahead=2)

        self.assertEqual(stats['skipped'], 1)
        self.assertEqual(stats['updated'], 0)
        calendar_service_class.return_value.update_event.assert_not_called()
