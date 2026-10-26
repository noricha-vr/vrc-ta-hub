from datetime import date, datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from django.core.cache import cache
from django.db.utils import OperationalError
from django.templatetags.static import static
from django.test import TestCase, override_settings
from django.urls import reverse

from ta_hub.index_cache import get_index_view_cache_key
from tests.factories import make_community, make_event, make_event_detail
from vket.models import VketCollaboration, VketParticipation
from vket.top_banner import get_active_top_banner

TOKYO = ZoneInfo('Asia/Tokyo')


class VketTopBannerTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.start = datetime(2026, 11, 10, 20, tzinfo=TOKYO)
        self.end = datetime(2026, 12, 21, tzinfo=TOKYO)
        self.collaboration = self.make_collaboration()

    def make_collaboration(self, slug='winter', **kwargs):
        defaults = {
            'name': '冬の技術学術WEEK', 'phase': 'locked',
            'period_start': date(2026, 12, 5), 'period_end': date(2026, 12, 20),
            'registration_deadline': date(2026, 11, 1), 'lt_deadline': date(2026, 11, 20),
            'hashtags': ['#冬の発表'],
            'settings_json': {'top_banner': {'start': self.start.isoformat(), 'image': 'ta_hub/images/banner.avif'}},
        }
        defaults.update(kwargs)
        return VketCollaboration.objects.create(slug=slug, **defaults)

    def update_banner(self, **kwargs):
        self.collaboration.settings_json['top_banner'].update(kwargs)
        self.collaboration.save(update_fields=['settings_json'])

    def get_page(self, now):
        with patch('ta_hub.views.timezone.now', return_value=now):
            return self.client.get(reverse('ta_hub:index'))

    def test_start_and_end_boundaries(self):
        self.update_banner(end=self.end.isoformat())
        for now, visible in [
            (self.start - timedelta(microseconds=1), False),
            (self.start, True),
            (self.end - timedelta(microseconds=1), True),
            (self.end, False),
            (self.end + timedelta(seconds=1), False),
        ]:
            with self.subTest(now=now):
                response = self.get_page(now)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['show_vket_notice'], visible)
                soup = BeautifulSoup(response.content, 'html.parser')
                self.assertEqual(soup.find(id='vket-top-banner') is not None, visible)
                self.assertEqual(soup.find(id='vket-timetable') is not None, visible)

    @override_settings(TIME_ZONE='UTC')
    def test_omitted_end_expires_at_tokyo_midnight_after_last_day(self):
        self.assertTrue(self.get_page(self.end - timedelta(seconds=1)).context['show_vket_notice'])
        self.assertFalse(self.get_page(self.end).context['show_vket_notice'])

    def test_start_switches_after_database_context_was_cached(self):
        before = self.get_page(self.start - timedelta(seconds=1))
        self.assertFalse(before.context['show_vket_notice'])
        key = get_index_view_cache_key(self.start.date())
        self.assertIsNotNone(cache.get(key))
        with patch('ta_hub.index_cache.Event.objects.filter', side_effect=AssertionError('キャッシュを再利用する')):
            response = self.get_page(self.start)
        self.assertTrue(response.context['show_vket_notice'])
        self.assertContains(response, 'タイムテーブルを見る')

    def test_banner_then_timetable_then_presentations_and_no_rotation(self):
        community = make_community(poster_image='posters/test.png')
        event = make_event(community, event_date=self.collaboration.period_start, start_time=time(21))
        make_event_detail(event, theme='テスト発表', status='approved')
        VketParticipation.objects.create(collaboration=self.collaboration, community=community, published_event=event)
        response = self.get_page(datetime(2026, 12, 5, 12, tzinfo=TOKYO))
        html = response.content.decode()
        self.assertLess(html.index('id="vket-top-banner"'), html.index('id="vket-timetable"'))
        self.assertLess(html.index('id="vket-timetable"'), html.index('予定されている発表一覧'))
        soup = BeautifulSoup(html, 'html.parser')
        banner = soup.find(id='vket-top-banner')
        self.assertNotIn('rotate', str(banner))
        self.assertNotIn('onmouseover', str(banner))
        self.assertNotIn('onmouseout', str(banner))
        self.assertEqual(banner.find('img')['src'], static('ta_hub/images/banner.avif'))
        self.assertEqual(banner.find('img').parent['href'], reverse('vket:detail', args=[self.collaboration.pk]))
        self.assertEqual(banner.find('a', string=lambda t: t and 'コラボの詳細を見る' in t)['href'], reverse('vket:detail', args=[self.collaboration.pk]))
        self.assertIn('12月5日〜12月20日は冬の技術学術WEEK！', banner.get_text())
        self.assertIn('#冬の発表', banner.get_text())
        self.assertIn('vket-top-banner-winter', banner['class'])
        timetable = soup.find(id='vket-timetable')
        self.assertEqual(timetable.select_one('.vket-timetable-block-body')['href'], reverse('community:detail', args=[community.pk]))
        self.assertIn('テスト発表', timetable.get_text())
        self.assertIn('21:00〜21:30 発表', timetable.get_text())
        self.assertEqual(timetable.select_one('.vket-timetable-rail-lt')['style'], 'top:0.0000%;height:50.0000%;')
        calendar = timetable.select_one('.vket-timetable-calendar')
        self.assertTrue(calendar['href'].startswith('https://www.google.com/calendar/render?action=TEMPLATE'))
        self.assertIn('20261205T210000/20261205T220000', calendar['href'])
        self.assertIn(community.name, calendar['aria-label'])
        self.assertEqual(calendar['target'], '_blank')
        self.assertTrue(soup.select_one('[data-vket-event="true"]'))
        self.assertEqual(len(timetable.select('.vket-week-panel:not([hidden])')), 1)

    def test_no_image_or_hashtags_and_no_javascript_still_shows_initial_week(self):
        self.collaboration.hashtags = []
        self.collaboration.save(update_fields=['hashtags'])
        self.update_banner(image=None)
        response = self.get_page(datetime(2026, 12, 9, 12, tzinfo=TOKYO))
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertIsNone(soup.find(id='vket-top-banner').find('img'))
        self.assertIsNone(soup.find(id='vket-week-2').get('hidden'))
        self.assertTrue(soup.find(id='vket-week-1').has_attr('hidden'))
        self.assertEqual(soup.find(attrs={'data-vket-week': 'vket-week-2'})['aria-pressed'], 'true')

    def test_image_url_official_link_and_explicit_theme(self):
        self.update_banner(image='https://example.com/banner.png', link='https://example.com/notice', theme='summer')
        banner = get_active_top_banner(self.start)
        self.assertEqual(banner['image'], 'https://example.com/banner.png')
        self.assertEqual(banner['link'], 'https://example.com/notice')
        self.assertEqual(banner['theme'], 'summer')
        self.assertEqual(banner['emoji'], '🌻')

    def test_theme_defaults_by_month(self):
        for month, expected in [(1, 'winter'), (3, 'winter'), (4, 'summer'), (7, 'summer'), (10, 'summer'), (11, 'winter')]:
            self.collaboration.period_start = date(2026, month, 1)
            self.collaboration.save(update_fields=['period_start'])
            with self.subTest(month=month):
                self.assertEqual(get_active_top_banner(self.start)['theme'], expected)

    def test_draft_is_never_displayed_and_earliest_active_period_wins(self):
        self.make_collaboration(slug='draft', phase='draft', period_start=date(2026, 11, 1))
        earlier = self.make_collaboration(slug='earlier', period_start=date(2026, 12, 1))
        self.assertEqual(get_active_top_banner(self.start)['collaboration'], earlier)
        earlier.phase = 'draft'
        earlier.save(update_fields=['phase'])
        self.assertEqual(get_active_top_banner(self.start)['collaboration'], self.collaboration)
        self.collaboration.phase = 'draft'
        self.collaboration.save(update_fields=['phase'])
        self.assertIsNone(get_active_top_banner(self.start))

    def test_collaboration_without_banner_setting_is_skipped_silently(self):
        for settings in [None, {}, {'top_banner': []}]:
            self.collaboration.settings_json = settings
            self.collaboration.save(update_fields=['settings_json'])
            with self.subTest(settings=settings), self.assertNoLogs('vket.top_banner', level='WARNING'):
                self.assertIsNone(get_active_top_banner(self.start))

    def test_invalid_or_missing_start_hides_banner_and_logs_one_warning(self):
        for settings in [{'top_banner': {}}, {'top_banner': {'start': 'bad'}}, {'top_banner': {'start': '2026-99-99T20:00:00'}}]:
            self.collaboration.settings_json = settings
            self.collaboration.save(update_fields=['settings_json'])
            with self.subTest(settings=settings), self.assertLogs('vket.top_banner', level='WARNING') as logs:
                self.assertIsNone(get_active_top_banner(self.start))
            self.assertEqual(len(logs.records), 1)

    def test_invalid_end_hides_banner(self):
        for end in ['bad', (self.start - timedelta(seconds=1)).isoformat()]:
            self.update_banner(end=end)
            with self.assertLogs('vket.top_banner', level='WARNING'):
                self.assertIsNone(get_active_top_banner(self.start))

    def test_settings_are_read_only(self):
        original = self.collaboration.settings_json.copy()
        get_active_top_banner(self.start)
        self.collaboration.refresh_from_db()
        self.assertEqual(self.collaboration.settings_json, original)

    def test_database_failure_in_banner_or_timetable_returns_page_without_notice(self):
        for target in ['ta_hub.views.get_active_top_banner', 'ta_hub.views.build_collaboration_timetable']:
            with self.subTest(target=target), patch(target, side_effect=OperationalError('unavailable')):
                response = self.get_page(self.start)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['database_degraded'])
            self.assertFalse(response.context['show_vket_notice'])
            self.assertIsNone(response.context['vket_timetable'])
            self.assertNotContains(response, 'id="vket-top-banner"')
