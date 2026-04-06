from datetime import date, time

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from community.models import Community
from event.models import Event, EventDetail


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class SitemapViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.approved_community = Community.objects.create(
            name="承認済み集会",
            start_time=time(22, 0),
            duration=60,
            weekdays=["Mon"],
            frequency="毎週",
            organizers="主催者A",
            status="approved",
        )
        self.pending_community = Community.objects.create(
            name="承認待ち集会",
            start_time=time(21, 0),
            duration=60,
            weekdays=["Tue"],
            frequency="毎週",
            organizers="主催者B",
            status="pending",
        )
        approved_event = Event.objects.create(
            community=self.approved_community,
            date=date(2026, 4, 1),
            start_time=time(22, 0),
            duration=60,
            weekday="Mon",
        )
        pending_event = Event.objects.create(
            community=self.pending_community,
            date=date(2026, 4, 2),
            start_time=time(21, 0),
            duration=60,
            weekday="Tue",
        )
        self.approved_detail = EventDetail.objects.create(
            event=approved_event,
            detail_type="LT",
            status="approved",
            speaker="発表者A",
            theme="承認済み発表",
            duration=15,
            start_time=time(22, 0),
        )
        self.pending_detail = EventDetail.objects.create(
            event=pending_event,
            detail_type="LT",
            status="pending",
            speaker="発表者B",
            theme="承認待ち発表",
            duration=15,
            start_time=time(21, 0),
        )

    def test_sitemap_lists_only_approved_records(self):
        response = self.client.get(reverse("sitemap:sitemap"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/xml")

        content = response.content.decode()
        self.assertIn("https://testserver/", content)
        self.assertIn(f"/event/detail/{self.approved_detail.pk}/", content)
        self.assertIn(f"/community/{self.approved_community.pk}/", content)
        self.assertNotIn(f"/event/detail/{self.pending_detail.pk}/", content)
        self.assertNotIn(f"/community/{self.pending_community.pk}/", content)
