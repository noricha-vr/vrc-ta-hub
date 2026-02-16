from datetime import date, time
from unittest.mock import patch, MagicMock

from django.test import TestCase
from googleapiclient.errors import HttpError

from community.models import Community
from event.community_cleanup import cleanup_community_future_data
from event.models import Event, RecurrenceRule


class CommunityCleanupServiceTest(TestCase):
    def setUp(self):
        self.community = Community.objects.create(
            name='Cleanup Test Community',
            status='approved',
            frequency='毎週',
            organizers='Tester',
            weekdays=['Mon'],
            start_time=time(21, 0),
        )
        self.from_date = date(2026, 2, 10)

        self.before_event = Event.objects.create(
            community=self.community,
            date=date(2026, 2, 9),
            start_time=time(21, 0),
            duration=60,
            weekday='MON',
        )
        self.after_event = Event.objects.create(
            community=self.community,
            date=date(2026, 2, 10),
            start_time=time(21, 0),
            duration=60,
            weekday='TUE',
        )

        self.rule = RecurrenceRule.objects.create(
            community=self.community,
            frequency='WEEKLY',
            interval=1,
            start_date=date(2026, 1, 1),
        )

    @patch('event.community_cleanup.GoogleCalendarService')
    def test_cleanup_deletes_future_data_and_google_events(self, mock_service_cls):
        mock_service = mock_service_cls.return_value
        mock_service.list_events.return_value = [
            {
                'id': 'gcal-target',
                'summary': self.community.name,
                'start': {'dateTime': '2026-02-10T21:00:00+09:00'},
            },
            {
                'id': 'gcal-before',
                'summary': self.community.name,
                'start': {'dateTime': '2026-02-09T21:00:00+09:00'},
            },
        ]

        stats = cleanup_community_future_data(
            community=self.community,
            from_date=self.from_date,
            delete_rules=True,
            delete_google_events=True,
            google_window_days=365,
            google_years=1,
        )

        self.assertEqual(stats['db_events'], 1)
        self.assertEqual(stats['rules'], 1)
        self.assertEqual(stats['google_events'], 1)
        self.assertTrue(Event.objects.filter(id=self.before_event.id).exists())
        self.assertFalse(Event.objects.filter(id=self.after_event.id).exists())
        self.assertFalse(RecurrenceRule.objects.filter(id=self.rule.id).exists())
        mock_service.delete_event.assert_called_once_with('gcal-target')

    @patch('event.community_cleanup.GoogleCalendarService')
    def test_cleanup_skips_summary_fallback_when_name_is_duplicated(self, mock_service_cls):
        Community.objects.create(
            name=self.community.name,
            status='approved',
            frequency='毎週',
            organizers='Another',
            weekdays=['Tue'],
            start_time=time(22, 0),
        )
        mock_service = mock_service_cls.return_value
        mock_service.list_events.return_value = [
            {
                'id': 'gcal-target',
                'summary': self.community.name,
                'start': {'dateTime': '2026-02-10T21:00:00+09:00'},
            }
        ]

        stats = cleanup_community_future_data(
            community=self.community,
            from_date=self.from_date,
            delete_rules=False,
            delete_google_events=True,
            google_window_days=365,
            google_years=1,
        )

        self.assertEqual(stats['google_events'], 0)
        mock_service.delete_event.assert_not_called()

    @patch('event.community_cleanup.GoogleCalendarService')
    def test_cleanup_ignores_404_on_google_delete(self, mock_service_cls):
        self.after_event.google_calendar_event_id = 'gone-event-id'
        self.after_event.save(update_fields=['google_calendar_event_id'])

        mock_service = mock_service_cls.return_value
        mock_service.list_events.return_value = []
        mock_resp = MagicMock()
        mock_resp.status = 404
        mock_service.delete_event.side_effect = HttpError(mock_resp, b'Not Found')

        stats = cleanup_community_future_data(
            community=self.community,
            from_date=self.from_date,
            delete_rules=False,
            delete_google_events=True,
            google_window_days=365,
            google_years=1,
        )

        self.assertEqual(stats['google_events'], 0)
