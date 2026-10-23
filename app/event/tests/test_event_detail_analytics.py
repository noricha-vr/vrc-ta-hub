"""発表の反響カードの表示・権限・集計範囲を検証する。"""
from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from analytics.models import PageAnalytics, YouTubeVideoStats
from tests.factories import (
    make_community,
    make_community_member,
    make_event,
    make_event_detail,
    make_user,
)

TODAY = date(2026, 10, 23)
VIDEO_ID = 'abcdefghijk'


class EventDetailAnalyticsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user(user_name='ev_owner', email='owner@example.com')
        cls.staff = make_user(user_name='ev_staff', email='staff@example.com')
        cls.other_owner = make_user(user_name='ev_other', email='other@example.com')
        cls.superuser = make_user(
            user_name='ev_admin', email='admin@example.com', is_staff=True, is_superuser=True,
        )
        cls.applicant = make_user(user_name='ev_applicant', email='applicant@example.com')
        cls.community = make_community(name='対象集会', owner=cls.owner)
        cls.other_community = make_community(name='別集会', owner=cls.other_owner)
        make_community_member(cls.community, cls.staff)
        cls.event = make_event(cls.community, event_date=TODAY - timedelta(days=10))
        cls.event_detail = make_event_detail(
            cls.event, status='approved', applicant=cls.applicant,
            contents='contents', youtube_url=f'https://youtu.be/{VIDEO_ID}',
        )
        cls.popular_detail = make_event_detail(cls.event, status='approved')
        make_event_detail(cls.event, status='approved', theme='閲覧なし')
        for source, pv in (
            ('google / organic', 35), ('t.co / referral', 12), ('(direct) / (none)', 3),
            ('vrc-ta-hub.com / referral', 2), ('chatgpt.com / ai-assistant', 2), ('poster / qr', 2),
        ):
            cls._page(pv=pv, source=source)
        cls._page(pv=1178, ago=31)
        cls._page(detail=cls.popular_detail, pv=100)
        for ago, views in ((31, 3070), (1, 3210)):
            YouTubeVideoStats.objects.create(
                event_detail=cls.event_detail, video_id=VIDEO_ID,
                date=TODAY - timedelta(days=ago), view_count=views,
            )

    def setUp(self):
        patcher = patch('analytics.services.timezone.localdate', return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)

    @classmethod
    def _page(cls, *, detail=None, community=None, kind=PageAnalytics.ContentType.EVENT_DETAIL,
              pv=1, ago=1, source='google / organic', path=None):
        detail = detail or cls.event_detail
        return PageAnalytics.objects.create(
            page_path=path or f'/event/detail/{detail.pk}/',
            date=TODAY - timedelta(days=ago), content_type=kind,
            community=community or cls.community, object_id=detail.pk,
            pv=pv, source_medium=source,
        )

    def _response(self, user=None, detail=None):
        if user is not None:
            self.client.force_login(user)
        return self.client.get(reverse('event:detail', kwargs={
            'pk': (detail or self.event_detail).pk,
        }))

    def _assert_no_old_charts(self, response):
        self.assertNotIn('daily_series', response.context)
        self.assertNotIn('source_breakdown', response.context)
        self.assertNotContains(response, 'analytics-daily-chart')
        self.assertNotContains(response, 'chart.umd.min.js')
        self.assertNotContains(response, 'google / organic')

    def _assert_no_manager_data(self, response):
        for key in ('analytics_rank', 'analytics_sources', 'analytics_dashboard_url',
                    'analytics_average_view_duration'):
            self.assertNotIn(key, response.context)
        for text in ('集会内の順位', 'どこから', '集会の解析を見る', '平均視聴時間'):
            self.assertNotContains(response, text)

    def test_owner_staff_and_superuser_see_full_card(self):
        for user in (self.owner, self.staff, self.superuser):
            with self.subTest(user=user.user_name):
                response = self._response(user)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['can_view_analytics'])
                self.assertEqual(response.context['analytics_article_views'], {'total': 1234, 'period': 56})
                self.assertEqual(response.context['analytics_video_views'], {'total': 3210, 'period': 140})
                self.assertEqual(response.context['analytics_rank'], {'rank': 2, 'total': 3})
                self.assertContains(response, 'この発表の反響', count=1)
                self.assertContains(response, '1,234')
                self.assertContains(response, '直近30日 56')
                self.assertContains(response, '3,210')
                self.assertContains(response, '直近30日 140')
                self.assertContains(response, '3本中 2位')
                self.assertEqual({row['label'] for row in response.context['analytics_sources']}, {
                    '検索', 'SNS', '直接', 'Hub 内', 'AI', 'その他',
                })
                self.assertContains(response, '検索 62%')
                self.assertContains(response, 'SNS 21%')
                dashboard_url = f"{reverse('analytics:dashboard')}?community={self.community.pk}"
                self.assertEqual(response.context['analytics_dashboard_url'], dashboard_url)
                self.assertContains(response, f'href="{dashboard_url}"')
                self.assertNotIn('analytics_average_view_duration', response.context)
                self._assert_no_old_charts(response)

    def test_applicant_analytics_context_has_counts_but_no_rank_sources_or_dashboard(self):
        with (
            patch('event.views.detail.analytics_services.get_source_categories') as sources,
            patch('event.views.detail.analytics_services.get_event_detail_rank') as rank,
        ):
            response = self._response(self.applicant)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_view_analytics'])
        self.assertEqual(response.context['analytics_article_views'], {'total': 1234, 'period': 56})
        self.assertEqual(response.context['analytics_video_views'], {'total': 3210, 'period': 140})
        self.assertContains(response, '記事の閲覧')
        self.assertContains(response, '動画の再生')
        sources.assert_not_called()
        rank.assert_not_called()
        self._assert_no_manager_data(response)
        self._assert_no_old_charts(response)

    def test_other_community_owner_has_no_analytics(self):
        self._assert_no_analytics(self._response(self.other_owner))

    def test_anonymous_has_no_analytics(self):
        self._assert_no_analytics(self._response())

    def _assert_no_analytics(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_view_analytics'])
        for key in ('analytics_article_views', 'analytics_video_views', 'analytics_has_data'):
            self.assertNotIn(key, response.context)
        self.assertNotContains(response, 'analytics-detail-card')
        self.assertNotContains(response, 'analytics_card_view')
        self._assert_no_manager_data(response)
        self._assert_no_old_charts(response)

    def test_applicant_of_pending_rejected_or_non_lt_has_no_analytics(self):
        for status, kind in (('pending', 'LT'), ('rejected', 'LT'),
                             ('approved', 'BLOG'), ('approved', 'SPECIAL')):
            with self.subTest(status=status, kind=kind):
                detail = make_event_detail(
                    self.event, applicant=self.applicant, status=status, detail_type=kind,
                )
                self._assert_no_analytics(self._response(self.applicant, detail))

    def test_no_video_omits_video_row_and_context(self):
        self.event_detail.youtube_url = ''
        self.event_detail.save(update_fields=['youtube_url'])
        response = self._response(self.owner)
        self.assertContains(response, '記事の閲覧')
        self.assertNotContains(response, '動画の再生')
        self.assertNotIn('analytics_video_views', response.context)

    def test_missing_video_period_is_not_rendered_as_zero(self):
        YouTubeVideoStats.objects.filter(date=TODAY - timedelta(days=31)).delete()
        response = self._response(self.owner)
        self.assertContains(response, '3,210')
        self.assertContains(response, '直近30日 集計待ち')
        self.assertIsNone(response.context['analytics_video_views']['period'])

    def test_missing_video_statistics_are_not_rendered_as_zero(self):
        YouTubeVideoStats.objects.all().delete()
        response = self._response(self.applicant)
        self.assertContains(response, '動画の再生')
        self.assertContains(response, '集計待ち', count=2)
        self.assertNotContains(response, 'None')

    def test_zero_video_period_is_rendered_as_zero(self):
        YouTubeVideoStats.objects.update(view_count=3210)
        response = self._response(self.owner)
        self.assertContains(response, '直近30日 0')
        self.assertNotContains(response, '集計待ち')

    def test_empty_data_has_one_message_and_no_empty_rows(self):
        PageAnalytics.objects.all().delete()
        YouTubeVideoStats.objects.all().delete()
        for user in (self.owner, self.applicant):
            with self.subTest(user=user.user_name):
                response = self._response(user)
                self.assertContains(response, 'まだ集計できるアクセスがありません', count=1)
                for text in ('記事の閲覧', '動画の再生', '集会内の順位', 'どこから', '集会の解析を見る'):
                    self.assertNotContains(response, text)
                self.assertContains(response, 'analytics_card_view')

    def test_video_data_alone_shows_counts(self):
        PageAnalytics.objects.all().delete()
        response = self._response(self.owner)
        self.assertContains(response, '記事の閲覧')
        self.assertContains(response, '直近30日 0')
        self.assertContains(response, '3,210')
        self.assertNotContains(response, '集会内の順位')
        self.assertNotContains(response, 'どこから')
        self.assertNotContains(response, 'まだ集計できるアクセスがありません')

    def test_old_article_data_still_shows_total_without_rank(self):
        PageAnalytics.objects.filter(date__gte=TODAY - timedelta(days=30)).delete()
        response = self._response(self.owner)
        self.assertEqual(response.context['analytics_article_views'], {'total': 1178, 'period': 0})
        self.assertContains(response, '1,178')
        self.assertNotContains(response, '集会内の順位')
        self.assertNotContains(response, 'どこから')

    def test_period_boundaries_exclude_today_and_include_thirtieth_day(self):
        self._page(ago=30, pv=4)
        self._page(ago=0, pv=1000)
        response = self._response(self.applicant)
        self.assertEqual(response.context['analytics_article_views']['period'], 60)

    def test_article_counts_and_sources_do_not_mix_other_content_or_community(self):
        self._page(kind=PageAnalytics.ContentType.COMMUNITY, pv=9999, path='/community-page/')
        self._page(community=self.other_community, pv=9999, path='/incorrect-community/')
        for user in (self.owner, self.applicant, self.superuser):
            with self.subTest(user=user.user_name):
                response = self._response(user)
                self.assertEqual(response.context['analytics_article_views'], {'total': 1234, 'period': 56})
                if user != self.applicant:
                    self.assertEqual(sum(row['pv'] for row in response.context['analytics_sources']), 56)

    def test_card_event_uses_page_type_and_handles_missing_gtag(self):
        response = self._response(self.applicant)
        self.assertContains(response, "gtag('event', 'analytics_card_view', {page_type: 'event_detail'});", count=1)
        self.assertContains(response, "typeof window.gtag === 'function'")
