from datetime import datetime, timedelta
from unittest import TestCase
from unittest.mock import MagicMock

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
