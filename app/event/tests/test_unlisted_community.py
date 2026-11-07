"""集会の掲載設定に依存しない開催回の公開テスト。"""

from django.test import TestCase
from django.urls import reverse

from tests.factories import make_community, make_event, make_event_detail


class UnlistedCommunityEventTest(TestCase):
    def test_future_event_and_presentation_remain_in_event_list(self):
        community = make_community(is_listed=False)
        event = make_event(community)
        detail = make_event_detail(event, status='approved')

        response = self.client.get(reverse('event:list'))

        self.assertEqual(response.status_code, 200)
        self.assertQuerySetEqual(response.context['object_list'], [event])
        self.assertIn(detail, response.context['object_list'][0].details.all())
        self.assertContains(response, detail.theme)
