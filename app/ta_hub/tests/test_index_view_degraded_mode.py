from unittest.mock import patch

from django.core.cache import cache
from django.db.utils import OperationalError
from django.test import Client, TestCase
from django.urls import reverse


class IndexViewDegradedModeTest(TestCase):
    def setUp(self):
        self.client = Client()
        cache.clear()

    def tearDown(self):
        cache.clear()

    @patch("ta_hub.views.Post.objects.filter")
    def test_index_view_returns_static_page_when_database_is_unavailable(self, mock_filter):
        mock_filter.side_effect = OperationalError("db unavailable")

        response = self.client.get(reverse("ta_hub:index"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["database_degraded"])
        self.assertEqual(response.context["vket_achievements"], [])
        self.assertEqual(response.context["upcoming_events"], [])
        self.assertEqual(response.context["upcoming_event_details"], [])
        self.assertEqual(response.context["special_events"], [])
        self.assertContains(response, "Googleカレンダーと連携して予定を管理")

    @patch("ta_hub.views.Post.objects.filter")
    def test_index_view_uses_cached_payload_when_database_is_unavailable(self, mock_filter):
        cache.set(
            "index_view_data_2026-04-04",
            {
                "vket_achievements": [],
                "upcoming_events": [],
                "upcoming_event_details": [],
                "special_events": [],
            },
            60,
        )
        mock_filter.side_effect = OperationalError("db unavailable")

        with patch("ta_hub.views.get_vrchat_today", return_value="2026-04-04"):
            response = self.client.get(reverse("ta_hub:index"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["database_degraded"])
        self.assertEqual(response.context["vket_achievements"], [])
        self.assertEqual(response.context["upcoming_events"], [])
        self.assertEqual(response.context["upcoming_event_details"], [])
        self.assertEqual(response.context["special_events"], [])
        mock_filter.assert_not_called()
