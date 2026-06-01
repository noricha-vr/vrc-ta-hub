from datetime import date

from django.test import TestCase

from community.models import Community
from event.models import Event, EventDetail

from analytics.models import PageAnalytics
from analytics.path_resolver import resolve_page_path


class ResolvePagePathTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.community = Community.objects.create(
            name='テスト集会',
            frequency='毎週',
            organizers='主催A',
        )
        cls.event = Event.objects.create(
            community=cls.community,
            date=date(2026, 5, 1),
            weekday='Thu',
        )
        cls.event_detail = EventDetail.objects.create(
            event=cls.event,
            detail_type='LT',
        )

    def test_community_path_resolves(self):
        result = resolve_page_path(f'/community/{self.community.pk}/')
        self.assertIsNotNone(result)
        self.assertEqual(result['content_type'], PageAnalytics.ContentType.COMMUNITY)
        self.assertEqual(result['community'], self.community)
        self.assertEqual(result['object_id'], self.community.pk)

    def test_event_detail_path_resolves_community_via_event(self):
        result = resolve_page_path(f'/event/detail/{self.event_detail.pk}/')
        self.assertIsNotNone(result)
        self.assertEqual(result['content_type'], PageAnalytics.ContentType.EVENT_DETAIL)
        # EventDetail に直接 community FK は無いので event 経由で解決される
        self.assertEqual(result['community'], self.community)
        self.assertEqual(result['object_id'], self.event_detail.pk)

    def test_community_path_with_query_string_is_none(self):
        self.assertIsNone(resolve_page_path(f'/community/{self.community.pk}/?x=1'))

    def test_community_path_without_trailing_slash_is_none(self):
        self.assertIsNone(resolve_page_path(f'/community/{self.community.pk}'))

    def test_non_numeric_pk_is_none(self):
        self.assertIsNone(resolve_page_path('/community/abc/'))

    def test_unrelated_path_is_none(self):
        self.assertIsNone(resolve_page_path('/about/'))

    def test_nonexistent_community_pk_is_none(self):
        self.assertIsNone(resolve_page_path('/community/999999/'))

    def test_nonexistent_event_detail_pk_is_none(self):
        self.assertIsNone(resolve_page_path('/event/detail/999999/'))

    def test_empty_path_is_none(self):
        self.assertIsNone(resolve_page_path(''))
