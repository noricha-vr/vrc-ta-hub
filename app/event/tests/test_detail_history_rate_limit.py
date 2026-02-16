from datetime import date, time, timedelta

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from community.models import Community
from event.models import Event, EventDetail


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class EventDetailHistoryRateLimitTest(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.url = reverse('event:detail_history')

        community = Community.objects.create(
            name='History Community',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved',
        )
        event = Event.objects.create(
            community=community,
            date=date.today() - timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon',
        )
        EventDetail.objects.create(
            event=event,
            detail_type='LT',
            status='approved',
            speaker='Approved Speaker',
            theme='Approved Theme',
            duration=15,
            start_time=time(22, 0),
        )

    def test_rate_limit_is_20_requests_per_10_minutes_per_ip(self):
        for _ in range(20):
            response = self.client.get(self.url, HTTP_X_FORWARDED_FOR='1.2.3.4')
            self.assertEqual(response.status_code, 200)

        blocked = self.client.get(self.url, HTTP_X_FORWARDED_FOR='1.2.3.4')
        self.assertEqual(blocked.status_code, 429)
        self.assertContains(
            blocked,
            'アクセスが集中しています。しばらくしてから再度お試しください。',
            status_code=429,
        )

    def test_rate_limit_is_independent_per_ip(self):
        for _ in range(20):
            self.client.get(self.url, HTTP_X_FORWARDED_FOR='1.2.3.4')

        blocked = self.client.get(self.url, HTTP_X_FORWARDED_FOR='1.2.3.4')
        self.assertEqual(blocked.status_code, 429)

        other_ip = self.client.get(self.url, HTTP_X_FORWARDED_FOR='5.6.7.8')
        self.assertEqual(other_ip.status_code, 200)


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class EventDetailHistoryQueryBloatPreventionTest(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.url = reverse('event:detail_history')

        community = Community.objects.create(
            name='History Community',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Test Organizer',
            status='approved',
        )
        event = Event.objects.create(
            community=community,
            date=date.today() - timedelta(days=7),
            start_time=time(22, 0),
            duration=60,
            weekday='Mon',
        )
        self.detail = EventDetail.objects.create(
            event=event,
            detail_type='LT',
            status='approved',
            speaker='Approved Speaker',
            theme='Interesting Theme',
            duration=15,
            start_time=time(22, 0),
        )

    def test_history_links_do_not_accumulate_duplicate_speaker_params(self):
        response = self.client.get(
            self.url,
            {
                'speaker': ['OldA', 'Approved Speaker'],
                'theme': 'Interesting Theme',
                'nocache': 'https://example.com',
                'page': '3',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)

        # speakerリンクは既存speakerを引き継がず、新しいspeakerのみ付与
        self.assertContains(response, '?theme=Interesting+Theme&speaker=Approved%20Speaker')
        self.assertNotContains(response, 'speaker=OldA&speaker=Approved%20Speaker&speaker=Approved%20Speaker')

        # communityリンクは検索条件を保持しつつ、不要キーは引き継がない
        self.assertContains(response, 'speaker=Approved+Speaker')
        self.assertContains(response, 'theme=Interesting+Theme')
        self.assertContains(response, 'community_name=History%20Community')
        self.assertNotContains(response, 'nocache=')
        self.assertNotContains(response, 'page=3&community_name=')
