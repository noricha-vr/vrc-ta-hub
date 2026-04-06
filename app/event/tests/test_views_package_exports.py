from django.test import SimpleTestCase

import event.views as views_module


class EventViewsPackageExportsTest(SimpleTestCase):
    def test_event_views_is_package_and_reexports_public_api(self):
        self.assertTrue(views_module.__file__.endswith("event/views/__init__.py"))

        expected_exports = [
            "EventListView",
            "EventDetailView",
            "EventDeleteView",
            "EventDetailCreateView",
            "EventDetailUpdateView",
            "EventDetailDeleteView",
            "EventMyList",
            "EventDetailPastList",
            "EventLogListView",
            "GoogleCalendarEventCreateView",
            "GenerateBlogView",
            "LTApplicationCreateView",
            "LTApplicationReviewView",
            "LTApplicationApproveView",
            "LTApplicationRejectView",
            "sync_calendar_events",
            "delete_outdated_events",
            "register_calendar_events",
            "extract_video_id",
            "extract_video_info",
            "_parse_youtube_time",
            "_get_bigquery_client",
            "can_manage_event_detail",
            "generate_blog",
            "logger",
        ]

        for export_name in expected_exports:
            with self.subTest(export_name=export_name):
                self.assertTrue(hasattr(views_module, export_name))

    def test_reexported_symbols_come_from_split_modules(self):
        self.assertEqual(views_module.EventListView.__module__, "event.views.list")
        self.assertEqual(views_module.EventDetailView.__module__, "event.views.detail")
        self.assertEqual(
            views_module.EventDetailCreateView.__module__,
            "event.views.crud",
        )
        self.assertEqual(views_module.GenerateBlogView.__module__, "event.views.blog")
        self.assertEqual(
            views_module.sync_calendar_events.__module__,
            "event.views.sync",
        )
