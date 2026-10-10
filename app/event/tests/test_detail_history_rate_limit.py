from datetime import UTC, date, datetime, time, timedelta
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from community.models import Community
from event.models import Event, EventDetail
from event.views.list import EventDetailPastList

# 上限値をビュー側の定数から参照し、値を変えてもテストが追従するようにする
RATE_LIMIT = EventDetailPastList.RATE_LIMIT_MAX_REQUESTS
WINDOW = EventDetailPastList.RATE_LIMIT_WINDOW_SECONDS


def _just_before_window_boundary():
    """いまの枠の終わる 1 秒前。実時間で数えると、ここで枠をまたいで回数がリセットされうる。"""
    now = int(timezone.now().timestamp())
    return datetime.fromtimestamp(now - now % WINDOW + WINDOW - 1, tz=UTC)


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class EventDetailHistoryRateLimitTest(TestCase):
    def setUp(self):
        cache.clear()
        # 窓は実時間の 10 分刻み。テスト中に境界をまたぐと回数がリセットされて落ちるので、
        # 時計を「境界の 1 秒前」に止める（最もまたぎやすい時刻でも安定することを保証する）。
        clock = mock.patch('event.views.list.timezone.now', return_value=_just_before_window_boundary())
        clock.start()
        self.addCleanup(clock.stop)
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

    def test_rate_limit_blocks_after_configured_requests_per_ip(self):
        for _ in range(RATE_LIMIT):
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
        for _ in range(RATE_LIMIT):
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
            # 発表一覧の既定表示は「資料あり」なので、本文を持たせて一覧に出す
            contents='記事本文があるため既定表示に含まれる。',
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

    def test_logged_in_mine_view_is_not_blocked_by_public_ip_limit(self):
        """公開一覧の IP 単位の上限に達しても、ログイン中の「自分の発表」は開ける。"""
        from tests.factories import make_user

        for _ in range(RATE_LIMIT):
            self.client.get(self.url, HTTP_X_FORWARDED_FOR='1.2.3.4')
        self.assertEqual(self.client.get(self.url, HTTP_X_FORWARDED_FOR='1.2.3.4').status_code, 429)

        self.client.force_login(make_user(user_name='mine_user', email='mine@example.com'))
        response = self.client.get(self.url, {'mine': '1'}, HTTP_X_FORWARDED_FOR='1.2.3.4')
        self.assertEqual(response.status_code, 200)
