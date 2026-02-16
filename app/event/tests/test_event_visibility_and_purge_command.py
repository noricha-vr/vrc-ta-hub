from datetime import date, time, timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

from community.models import Community
from event.models import Event, EventDetail, RecurrenceRule


def _create_test_image():
    """テスト用の最小PNGバイナリを返す。"""
    import struct
    import zlib

    def _chunk(chunk_type, data):
        c = chunk_type + data
        crc = struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + c + crc

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw_data = b"\x00\xff\x00\x00"
    idat_data = zlib.compress(raw_data)
    return signature + _chunk(b"IHDR", ihdr_data) + _chunk(b"IDAT", idat_data) + _chunk(b"IEND", b"")


class EventVisibilityFilterRegressionTest(TestCase):
    """終了済み/非承認コミュニティのイベントが公開面に出ないことを確認する。"""

    def setUp(self):
        self.client = Client()
        cache.clear()
        self.today = date.today()
        image_content = _create_test_image()

        self.active_community = Community.objects.create(
            name="Active Community",
            start_time=time(21, 0),
            duration=60,
            weekdays=["Mon"],
            frequency="Every week",
            organizers="Active Organizer",
            status="approved",
            poster_image=SimpleUploadedFile("active.png", image_content, content_type="image/png"),
        )
        self.ended_community = Community.objects.create(
            name="Ended Community",
            start_time=time(22, 0),
            duration=60,
            weekdays=["Tue"],
            frequency="Every week",
            organizers="Ended Organizer",
            status="approved",
            end_at=self.today,
            poster_image=SimpleUploadedFile("ended.png", image_content, content_type="image/png"),
        )
        self.pending_community = Community.objects.create(
            name="Pending Community",
            start_time=time(23, 0),
            duration=60,
            weekdays=["Wed"],
            frequency="Every week",
            organizers="Pending Organizer",
            status="pending",
            poster_image=SimpleUploadedFile("pending.png", image_content, content_type="image/png"),
        )

        self.active_event = Event.objects.create(
            community=self.active_community,
            date=self.today + timedelta(days=2),
            start_time=time(21, 0),
            duration=60,
            weekday="MON",
        )
        self.ended_event = Event.objects.create(
            community=self.ended_community,
            date=self.today + timedelta(days=2),
            start_time=time(22, 0),
            duration=60,
            weekday="TUE",
        )
        self.pending_event = Event.objects.create(
            community=self.pending_community,
            date=self.today + timedelta(days=2),
            start_time=time(23, 0),
            duration=60,
            weekday="WED",
        )

        EventDetail.objects.create(
            event=self.active_event,
            detail_type="LT",
            status="approved",
            speaker="Active Speaker",
            theme="Active Theme",
            start_time=time(21, 0),
        )
        EventDetail.objects.create(
            event=self.ended_event,
            detail_type="LT",
            status="approved",
            speaker="Ended Speaker",
            theme="Ended Theme",
            start_time=time(22, 0),
        )

    def tearDown(self):
        cache.clear()

    def test_event_list_excludes_ended_and_pending_communities(self):
        response = self.client.get(reverse("event:list"))
        self.assertEqual(response.status_code, 200)

        event_ids = [event.id for event in response.context["events"]]
        self.assertIn(self.active_event.id, event_ids)
        self.assertNotIn(self.ended_event.id, event_ids)
        self.assertNotIn(self.pending_event.id, event_ids)

    def test_index_excludes_ended_and_pending_communities(self):
        response = self.client.get(reverse("ta_hub:index"))
        self.assertEqual(response.status_code, 200)

        upcoming_events = response.context.get("upcoming_events", [])
        event_ids = [event["id"] for event in upcoming_events]
        self.assertIn(self.active_event.id, event_ids)
        self.assertNotIn(self.ended_event.id, event_ids)
        self.assertNotIn(self.pending_event.id, event_ids)

    def test_public_api_excludes_ended_communities(self):
        event_response = self.client.get("/api/v1/event/")
        self.assertEqual(event_response.status_code, 200)
        event_payload = event_response.json()
        event_results = event_payload["results"] if isinstance(event_payload, dict) else event_payload
        returned_event_ids = [item["id"] for item in event_results]
        self.assertIn(self.active_event.id, returned_event_ids)
        self.assertNotIn(self.ended_event.id, returned_event_ids)
        self.assertNotIn(self.pending_event.id, returned_event_ids)

        detail_response = self.client.get("/api/v1/event_detail/")
        self.assertEqual(detail_response.status_code, 200)
        detail_payload = detail_response.json()
        detail_results = detail_payload["results"] if isinstance(detail_payload, dict) else detail_payload
        returned_speakers = [item["speaker"] for item in detail_results]
        self.assertIn("Active Speaker", returned_speakers)
        self.assertNotIn("Ended Speaker", returned_speakers)


class PurgeCommunityEventsCommandTest(TestCase):
    """purge_community_events コマンドの回帰テスト。"""

    def setUp(self):
        self.from_date = date(2026, 2, 10)
        self.target_name = "AI集会テックWeek"

        self.target_community = Community.objects.create(
            name=self.target_name,
            start_time=time(21, 0),
            duration=60,
            weekdays=["Mon"],
            frequency="Every week",
            organizers="Target Organizer",
            status="approved",
        )
        self.other_community = Community.objects.create(
            name="Other Community",
            start_time=time(21, 0),
            duration=60,
            weekdays=["Mon"],
            frequency="Every week",
            organizers="Other Organizer",
            status="approved",
        )

        self.before_event = Event.objects.create(
            community=self.target_community,
            date=date(2026, 2, 9),
            start_time=time(21, 0),
            duration=60,
            weekday="MON",
        )
        self.after_event = Event.objects.create(
            community=self.target_community,
            date=date(2026, 2, 10),
            start_time=time(21, 0),
            duration=60,
            weekday="TUE",
        )
        self.other_event = Event.objects.create(
            community=self.other_community,
            date=date(2026, 2, 10),
            start_time=time(21, 0),
            duration=60,
            weekday="TUE",
        )

        self.rule = RecurrenceRule.objects.create(
            community=self.target_community,
            frequency="WEEKLY",
            interval=1,
            start_date=date(2026, 1, 1),
        )
        self.master = Event.objects.create(
            community=self.target_community,
            date=date(2026, 1, 1),
            start_time=time(21, 0),
            duration=60,
            weekday="THU",
            recurrence_rule=self.rule,
            is_recurring_master=True,
        )
        self.future_instance = Event.objects.create(
            community=self.target_community,
            date=date(2026, 2, 17),
            start_time=time(21, 0),
            duration=60,
            weekday="TUE",
            recurring_master=self.master,
        )

    @patch("event.management.commands.purge_community_events.GoogleCalendarService")
    def test_dry_run_does_not_delete_data(self, mock_service_cls):
        mock_service = mock_service_cls.return_value
        mock_service.list_events.return_value = [
            {
                "id": "gcal-target",
                "summary": self.target_name,
                "start": {"dateTime": "2026-02-10T12:00:00+09:00"},
            }
        ]

        call_command(
            "purge_community_events",
            f"--community={self.target_name}",
            f"--from-date={self.from_date.isoformat()}",
            "--delete-rules",
            "--dry-run",
            "--google-window-days=365",
            "--google-years=1",
        )

        self.assertTrue(Event.objects.filter(id=self.after_event.id).exists())
        self.assertTrue(RecurrenceRule.objects.filter(id=self.rule.id).exists())
        mock_service.delete_event.assert_not_called()

    @patch("event.management.commands.purge_community_events.GoogleCalendarService")
    def test_execute_deletes_target_range_and_rules(self, mock_service_cls):
        mock_service = mock_service_cls.return_value
        mock_service.list_events.return_value = [
            {
                "id": "gcal-target",
                "summary": self.target_name,
                "start": {"dateTime": "2026-02-10T12:00:00+09:00"},
            },
            {
                "id": "gcal-before",
                "summary": self.target_name,
                "start": {"dateTime": "2026-02-09T12:00:00+09:00"},
            },
            {
                "id": "gcal-other-community",
                "summary": "Other Community",
                "start": {"dateTime": "2026-02-10T12:00:00+09:00"},
            },
        ]

        call_command(
            "purge_community_events",
            f"--community={self.target_name}",
            f"--from-date={self.from_date.isoformat()}",
            "--delete-rules",
            "--google-window-days=365",
            "--google-years=1",
        )

        self.assertTrue(Event.objects.filter(id=self.before_event.id).exists())
        self.assertFalse(Event.objects.filter(id=self.after_event.id).exists())
        self.assertTrue(Event.objects.filter(id=self.other_event.id).exists())
        self.assertFalse(RecurrenceRule.objects.filter(id=self.rule.id).exists())

        deleted_ids = {call.args[0] for call in mock_service.delete_event.call_args_list}
        self.assertEqual(deleted_ids, {"gcal-target"})
