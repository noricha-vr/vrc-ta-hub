from datetime import datetime, timedelta
from unittest import TestCase
from unittest.mock import MagicMock

from googleapiclient.errors import HttpError

from event.google_calendar import GoogleCalendarService


class GoogleCalendarListPaginationTest(TestCase):
    def test_list_events_fetches_all_pages(self):
        service = GoogleCalendarService.__new__(GoogleCalendarService)
        service.calendar_id = "test-calendar"
        service.service = MagicMock()

        first_page = {
            "items": [{"id": "evt-1"}],
            "nextPageToken": "page-2",
        }
        second_page = {
            "items": [{"id": "evt-2"}],
        }

        events_resource = service.service.events.return_value
        events_resource.list.side_effect = [
            MagicMock(execute=MagicMock(return_value=first_page)),
            MagicMock(execute=MagicMock(return_value=second_page)),
        ]

        now = datetime.now()
        result = service.list_events(
            max_results=1,
            time_min=now,
            time_max=now + timedelta(days=1),
        )

        self.assertEqual([item["id"] for item in result], ["evt-1", "evt-2"])
        self.assertEqual(events_resource.list.call_count, 2)
        first_kwargs = events_resource.list.call_args_list[0].kwargs
        second_kwargs = events_resource.list.call_args_list[1].kwargs
        self.assertIsNone(first_kwargs["pageToken"])
        self.assertEqual(second_kwargs["pageToken"], "page-2")

    def test_create_event_logs_http_error(self):
        service = GoogleCalendarService.__new__(GoogleCalendarService)
        service.calendar_id = "test-calendar"
        service.service = MagicMock()

        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_resp.reason = "Internal Server Error"

        events_resource = service.service.events.return_value
        events_resource.insert.return_value.execute.side_effect = HttpError(
            mock_resp,
            b"calendar failure",
        )

        with self.assertLogs("event.google_calendar", level="ERROR") as log_context:
            with self.assertRaises(HttpError):
                service.create_event(
                    summary="テストイベント",
                    start_time=datetime(2026, 1, 1, 22, 0),
                    end_time=datetime(2026, 1, 1, 23, 0),
                )

        self.assertIn(
            "Google Calendar event creation failed",
            "\n".join(log_context.output),
        )
