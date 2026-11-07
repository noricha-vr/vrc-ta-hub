"""非掲載集会の開催予定をトップから除外しないことを検証する。"""

from datetime import timedelta

from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.utils import timezone

from ta_hub.index_cache import build_index_database_context, get_index_view_cache_key
from tests.factories import make_community, make_event, make_event_detail


class UnlistedCommunityIndexTest(TestCase):
    def test_future_event_and_presentation_remain_in_index_context(self):
        cache.clear()
        self.addCleanup(cache.clear)
        today = timezone.localdate()
        community = make_community(is_listed=False, poster_image='poster/hub.jpg')
        event = make_event(community, event_date=today + timedelta(days=1))
        detail = make_event_detail(event, status='approved')
        request = RequestFactory().get('/')

        context = build_index_database_context(request, today, get_index_view_cache_key(today))

        self.assertEqual([item['id'] for item in context['upcoming_events']], [event.pk])
        self.assertEqual([item['id'] for item in context['upcoming_event_details']], [detail.pk])
