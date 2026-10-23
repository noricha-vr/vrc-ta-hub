"""イベント一覧の応答が未来イベントの総数に比例して重くならないことを確認する。"""

from datetime import timedelta, time
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from community.models import Community
from event.models import Event, EventDetail
from event_calendar import calendar_utils
from event.views.list import EventListView
from utils.vrchat_time import get_vrchat_today


class EventListPerformanceTest(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.community = Community.objects.create(
            name="Perf Community",
            start_time=time(21, 0),
            duration=60,
            weekdays=["Mon"],
            frequency="Every week",
            organizers="Perf Organizer",
            status="approved",
        )
        today = get_vrchat_today()
        self.events = [
            Event.objects.create(
                community=self.community,
                date=today + timedelta(days=i),
                start_time=time(21, 0),
                duration=60,
                weekday="MON",
            )
            for i in range(EventListView.paginate_by + 15)
        ]
        EventDetail.objects.create(
            event=self.events[0],
            detail_type="LT",
            status="approved",
            speaker="Perf Speaker",
            theme="Perf Theme",
            start_time=time(21, 0),
        )

    def test_calendar_url_is_built_only_for_displayed_page(self):
        with patch(
            "event.views.list.build_google_calendar_url",
            wraps=calendar_utils.build_google_calendar_url,
        ) as build_mock:
            response = self.client.get(reverse("event:list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(build_mock.call_count, EventListView.paginate_by)
        # 承認済み発表を含むカレンダー URL がテンプレートに出ている
        self.assertContains(response, "https://www.google.com/calendar/render?")
        self.assertContains(response, "Perf%20Speaker")

    def test_calendar_url_does_not_touch_cache_per_event(self):
        with patch.object(calendar_utils.cache, "get", wraps=calendar_utils.cache.get) as cache_get:
            self.client.get(reverse("event:list"))

        calendar_keys = [
            call.args[0] for call in cache_get.call_args_list
            if str(call.args[0]).startswith("google_calendar_url_")
        ]
        self.assertEqual(calendar_keys, [])

    def test_query_count_does_not_grow_with_future_events(self):
        url = reverse("event:list")
        self.client.get(url)  # 初回だけの遅延初期化を除く
        with CaptureQueriesContext(connection) as before:
            self.client.get(url)

        today = get_vrchat_today()
        for i in range(len(self.events), len(self.events) + 60):
            Event.objects.create(
                community=self.community,
                date=today + timedelta(days=i),
                start_time=time(21, 0),
                duration=60,
                weekday="MON",
            )

        with CaptureQueriesContext(connection) as after:
            self.client.get(url)

        self.assertEqual(len(after.captured_queries), len(before.captured_queries))
