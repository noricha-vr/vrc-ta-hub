"""YouTube Analytics の日別取得・冪等同期・加重平均・権限付き表示を検証する。"""
import logging
from datetime import date, timedelta
from unittest.mock import call, patch

from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError
from httplib2 import Response

from analytics.models import PageAnalytics, YouTubeVideoAnalytics
from analytics.templatetags.analytics_filters import watch_duration
from analytics.video_stats import get_video_watch_times, sync_youtube_analytics
from analytics.youtube_client import fetch_video_analytics
from tests.factories import (
    make_community, make_community_member, make_event, make_event_detail, make_user,
    make_youtube_video_analytics,
)

TODAY = date(2026, 10, 23)
TARGET = TODAY - timedelta(days=1)
VIDEO_ID = 'abcdefghijk'
SECOND_ID = 'lmnopqrstuv'
OAUTH_SETTINGS = {
    'YOUTUBE_CLIENT_ID': 'test-client-id',
    'YOUTUBE_CLIENT_SECRET': 'test-client-secret',
    'YOUTUBE_ANALYTICS_REFRESH_TOKEN': 'test-refresh-token',
}


@override_settings(**OAUTH_SETTINGS)
class FetchVideoAnalyticsTests(SimpleTestCase):
    @patch('analytics.youtube_client.build')
    def test_daily_query_and_pagination_at_200_rows(self, build):
        query = build.return_value.reports.return_value.query
        query.return_value.execute.side_effect = [
            {'rows': [[f'video{i:06d}', 10, 25.5, 153] for i in range(200)]},
            {'rows': [[SECOND_ID, 20, 40, 120]]},
        ]
        result = fetch_video_analytics(TARGET)
        self.assertEqual(len(result), 201)
        self.assertEqual(result[SECOND_ID], {
            'views': 20, 'estimated_minutes_watched': 40.0,
            'average_view_duration_seconds': 120.0,
        })
        common = dict(
            ids='channel==MINE', startDate='2026-10-22', endDate='2026-10-22',
            metrics='views,estimatedMinutesWatched,averageViewDuration',
            dimensions='video', sort='-views', maxResults=200,
        )
        self.assertEqual(query.call_args_list, [
            call(**common, startIndex=1), call(**common, startIndex=201),
        ])
        args, kwargs = build.call_args
        self.assertEqual(args, ('youtubeAnalytics', 'v2'))
        self.assertFalse(kwargs['cache_discovery'])
        credentials = kwargs['credentials']
        self.assertEqual(credentials.refresh_token, 'test-refresh-token')
        self.assertEqual(credentials.client_id, 'test-client-id')
        self.assertEqual(credentials.client_secret, 'test-client-secret')
        self.assertEqual(credentials.token_uri, 'https://oauth2.googleapis.com/token')

    @patch('analytics.youtube_client.build')
    def test_exactly_200_rows_requests_empty_final_page(self, build):
        query = build.return_value.reports.return_value.query
        query.return_value.execute.side_effect = [
            {'rows': [[f'video{i:06d}', 0, 0, 0] for i in range(200)]}, {},
        ]
        self.assertEqual(len(fetch_video_analytics(TARGET)), 200)
        self.assertEqual(query.call_count, 2)

    @patch('analytics.youtube_client.build')
    def test_each_missing_setting_skips_without_building_client(self, build):
        for name in OAUTH_SETTINGS:
            with self.subTest(name=name), override_settings(**{name: ''}), \
                    self.assertLogs('analytics', level='WARNING'):
                self.assertEqual(fetch_video_analytics(TARGET), {})
        build.assert_not_called()

    @patch('analytics.youtube_client.build')
    def test_empty_report_is_empty(self, build):
        build.return_value.reports.return_value.query.return_value.execute.return_value = {}
        self.assertEqual(fetch_video_analytics(TARGET), {})

    @patch('analytics.youtube_client.build')
    def test_errors_hide_credentials_even_in_tracebacks(self, build):
        secret = 'private-test-value'
        errors = [
            HttpError(Response({'status': '403'}), secret.encode(), uri=f'https://invalid/?key={secret}'),
            RefreshError(secret), RuntimeError(secret),
        ]
        for error in errors:
            for phase in ('build', 'execute'):
                with self.subTest(error=type(error), phase=phase):
                    build.side_effect = error if phase == 'build' else None
                    execute = build.return_value.reports.return_value.query.return_value.execute
                    execute.side_effect = error if phase == 'execute' else None
                    with self.assertLogs('analytics', level='ERROR') as logs:
                        try:
                            fetch_video_analytics(TARGET)
                        except RuntimeError as exc:
                            self.assertNotIn(secret, str(exc))
                            if isinstance(error, HttpError):
                                self.assertIn('HTTP 403', str(exc))
                            logging.getLogger('analytics').exception('同期失敗')
                        else:
                            self.fail('API の例外が伝播しなかった')
                    self.assertNotIn(secret, '\n'.join(logs.output))
                    for value in OAUTH_SETTINGS.values():
                        self.assertNotIn(value, '\n'.join(logs.output))


class AnalyticsDataTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user(user_name='analytics-owner', email='analytics-owner@example.com')
        cls.presenter = make_user(user_name='analytics-presenter', email='analytics-presenter@example.com')
        cls.community = make_community(owner=cls.owner)
        cls.event = make_event(cls.community, event_date=TODAY - timedelta(days=10))
        cls.detail = make_event_detail(
            cls.event, applicant=cls.presenter, status='approved',
            youtube_url=f'https://youtu.be/{VIDEO_ID}',
        )
        cls.shared = make_event_detail(
            cls.event, status='approved', youtube_url=f'https://youtube.com/watch?v={VIDEO_ID}&t=20',
        )
        cls.second = make_event_detail(
            cls.event, status='approved', youtube_url=f'https://youtu.be/{SECOND_ID}',
        )


@override_settings(**OAUTH_SETTINGS)
class SyncYouTubeAnalyticsTests(AnalyticsDataTestCase):
    @patch('analytics.youtube_client.build')
    def test_three_days_are_individual_queries_and_rerun_updates_rows(self, build):
        query = build.return_value.reports.return_value.query
        query.return_value.execute.return_value = {
            'rows': [[VIDEO_ID, 10, 20, 120], ['unlinked001', 2, 3, 90]],
        }
        # 過去日でも公開統計と異なり上書きできる。
        target = date(2026, 9, 1)
        self.assertEqual(sync_youtube_analytics(target), 6)
        self.assertEqual(build.call_count, 3)
        self.assertEqual([c.kwargs['startDate'] for c in query.call_args_list], [
            '2026-08-30', '2026-08-31', '2026-09-01',
        ])
        self.assertTrue(all(c.kwargs['startDate'] == c.kwargs['endDate'] for c in query.call_args_list))
        self.assertEqual(YouTubeVideoAnalytics.objects.count(), 6)
        linked = YouTubeVideoAnalytics.objects.get(video_id=VIDEO_ID, date=target)
        self.assertEqual(linked.event_detail_id, self.detail.pk)
        self.assertIsNone(YouTubeVideoAnalytics.objects.get(video_id='unlinked001', date=target).event_detail_id)
        query.return_value.execute.return_value = {
            'rows': [[VIDEO_ID, 20, 50, 150], ['unlinked001', 2, 3, 90]],
        }
        self.assertEqual(sync_youtube_analytics(target), 6)
        self.assertEqual(YouTubeVideoAnalytics.objects.count(), 6)
        linked.refresh_from_db()
        self.assertEqual((linked.views, linked.estimated_minutes_watched,
                          linked.average_view_duration_seconds), (20, 50, 150))

    @patch('analytics.youtube_client.build')
    def test_missing_configuration_skips_all_days(self, build):
        with override_settings(YOUTUBE_ANALYTICS_REFRESH_TOKEN=''), \
                self.assertLogs('analytics', level='WARNING'):
            self.assertEqual(sync_youtube_analytics(TARGET), 0)
        build.assert_not_called()
        self.assertFalse(YouTubeVideoAnalytics.objects.exists())

    @patch('analytics.youtube_client.build')
    def test_partial_fetch_failure_does_not_save_any_of_three_days(self, build):
        build.return_value.reports.return_value.query.return_value.execute.side_effect = [
            {'rows': [[VIDEO_ID, 10, 20, 120]]}, RuntimeError('private-test-value'),
        ]
        with self.assertRaises(RuntimeError):
            sync_youtube_analytics(TARGET)
        self.assertFalse(YouTubeVideoAnalytics.objects.exists())

    def test_unique_video_date_and_set_null_preserve_history(self):
        row = make_youtube_video_analytics(date=TARGET, event_detail=self.detail, views=2 ** 40)
        self.assertIsNotNone(row.created_at)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_youtube_video_analytics(date=TARGET, event_detail=self.shared)
        self.event.delete()
        row.refresh_from_db()
        self.assertIsNone(row.event_detail_id)
        self.assertEqual(row.views, 2 ** 40)

    @override_settings(REQUEST_TOKEN='test-token')
    @patch('analytics.views.sync_youtube_stats', return_value=0)
    @patch('analytics.views.fetch_poster_click_report', return_value=[])
    @patch('analytics.views.fetch_page_report')
    @patch('analytics.youtube_client.build')
    def test_api_failure_returns_502_and_keeps_ga4(self, build, pages, _poster, _stats):
        pages.return_value = [{
            'page_path': '/', 'date': TARGET, 'source_medium': '(direct) / (none)',
            'campaign': '(not set)', 'pv': 10, 'users': 8, 'sessions': 9,
        }]
        build.return_value.reports.return_value.query.return_value.execute.side_effect = HttpError(
            Response({'status': '403'}), b'private-test-value', uri='https://invalid/private-test-value',
        )
        with self.assertLogs('analytics', level='ERROR') as logs:
            response = self.client.get(reverse('analytics:sync'), {'date': TARGET.isoformat()},
                                       HTTP_REQUEST_TOKEN='test-token')
        self.assertEqual(response.status_code, 502)
        self.assertIn('YouTubeAnalytics: failed', response.content.decode())
        self.assertEqual(PageAnalytics.objects.get().pv, 10)
        self.assertFalse(YouTubeVideoAnalytics.objects.exists())
        self.assertNotIn('private-test-value', '\n'.join(logs.output) + response.content.decode())
        self.assertFalse(logs.records[0].exc_info)

    @override_settings(REQUEST_TOKEN='test-token', YOUTUBE_CLIENT_ID='')
    @patch('analytics.views.sync_youtube_stats', return_value=0)
    @patch('analytics.views.fetch_poster_click_report', return_value=[])
    @patch('analytics.views.fetch_page_report', return_value=[])
    @patch('analytics.youtube_client.build')
    def test_skip_returns_200_and_zero(self, build, _pages, _poster, _stats):
        response = self.client.get(reverse('analytics:sync'), HTTP_REQUEST_TOKEN='test-token')
        self.assertEqual(response.status_code, 200)
        self.assertIn('YouTubeAnalytics: 0', response.content.decode())
        build.assert_not_called()

    @override_settings(REQUEST_TOKEN='test-token')
    @patch('analytics.views.sync_youtube_stats', return_value=0)
    @patch('analytics.views.fetch_poster_click_report', return_value=[])
    @patch('analytics.views.fetch_page_report', return_value=[])
    @patch('analytics.youtube_client.build')
    def test_success_response_reports_saved_count(self, build, _pages, _poster, _stats):
        build.return_value.reports.return_value.query.return_value.execute.return_value = {
            'rows': [[VIDEO_ID, 10, 20, 120]],
        }
        response = self.client.get(reverse('analytics:sync'), {'date': TARGET.isoformat()},
                                   HTTP_REQUEST_TOKEN='test-token')
        self.assertEqual(response.status_code, 200)
        self.assertIn('YouTubeAnalytics: 3', response.content.decode())


@patch('analytics.video_stats.timezone.localdate', return_value=TODAY)
class VideoWatchTimeTests(AnalyticsDataTestCase):
    def test_weighted_average_and_shared_video_deduplication(self, _today):
        # 日別平均の単純平均 (60 + 240) / 2 = 150 とは異なる 222 秒。
        make_youtube_video_analytics(date=TARGET, views=1, estimated_minutes_watched=1,
                                     average_view_duration_seconds=60)
        make_youtube_video_analytics(date=TARGET - timedelta(days=1), views=9,
                                     estimated_minutes_watched=36, average_view_duration_seconds=240)
        make_youtube_video_analytics(video_id=SECOND_ID, date=TARGET, views=30,
                                     estimated_minutes_watched=43, average_view_duration_seconds=86)
        by_detail, combined = get_video_watch_times([self.detail.pk, self.shared.pk, self.second.pk])
        self.assertEqual(by_detail, {self.detail.pk: 222, self.shared.pk: 222, self.second.pk: 86})
        self.assertEqual(combined, 120)  # (37 + 43) * 60 / (10 + 30)
        self.assertEqual(get_video_watch_times([self.second.pk]), ({self.second.pk: 86}, 86))

    def test_zero_views_and_missing_data_are_absent(self, _today):
        make_youtube_video_analytics(date=TARGET, views=0)
        self.assertEqual(get_video_watch_times([self.detail.pk, self.second.pk]), ({}, None))
        with self.assertNumQueries(0):
            self.assertEqual(get_video_watch_times([]), ({}, None))

    def test_date_boundaries_include_first_day_and_exclude_today(self, _today):
        for ago, minutes in ((0, 1000), (1, 1), (7, 3), (8, 900), (30, 5), (31, 1000)):
            make_youtube_video_analytics(date=TODAY - timedelta(days=ago), views=1,
                                         estimated_minutes_watched=minutes)
        self.assertEqual(get_video_watch_times([self.detail.pk], days=7)[1], 120)
        self.assertEqual(get_video_watch_times([self.detail.pk], days=30)[1], 13635)

    def test_invalid_and_deleted_details_are_excluded(self, _today):
        make_youtube_video_analytics(date=TARGET, views=1, estimated_minutes_watched=1)
        self.detail.delete()
        self.shared.youtube_url = 'https://example.com/'
        self.shared.save(update_fields=['youtube_url'])
        self.assertEqual(get_video_watch_times([self.detail.pk, self.shared.pk]), ({}, None))
        with self.assertRaises(ValueError):
            get_video_watch_times([self.second.pk], days=0)


class WatchDurationTests(SimpleTestCase):
    def test_formats_seconds_minutes_hours_and_missing_value(self):
        for seconds, expected in ((None, ''), (0, '0:00'), (59.9, '0:59'),
                                  (60, '1:00'), (3599, '59:59'), (3600, '1:00:00'),
                                  (3661, '1:01:01')):
            with self.subTest(seconds=seconds):
                self.assertEqual(watch_duration(seconds), expected)


@patch('analytics.services.timezone.localdate', return_value=TODAY)
class WatchTimeDisplayTests(AnalyticsDataTestCase):
    def setUp(self):
        PageAnalytics.objects.bulk_create([PageAnalytics(
            page_path=f'/event/detail/{self.detail.pk}/', date=TARGET,
            content_type='event_detail', community=self.community, object_id=self.detail.pk,
            pv=10, source_medium='google / organic',
        )])
        self.detail_url = reverse('event:detail', kwargs={'pk': self.detail.pk})
        self.community_url = reverse('community:detail', kwargs={'pk': self.community.pk})
        self.dashboard_url = reverse('analytics:dashboard')

    def _watch(self, **extra):
        return make_youtube_video_analytics(
            date=TARGET, views=10, estimated_minutes_watched=22, **extra,
        )

    def test_manager_sees_average_in_all_three_places_without_public_stats(self, _today):
        self._watch()
        self.client.force_login(self.owner)
        detail = self.client.get(self.detail_url)
        community = self.client.get(self.community_url)
        dashboard = self.client.get(self.dashboard_url)
        self.assertEqual(detail.context['analytics_average_view_duration'], 132)
        self.assertEqual(community.context['community_analytics']['average_watch_time'], 132)
        self.assertEqual(dashboard.context['average_watch_time_by_detail'], {self.detail.pk: 132})
        for response in (detail, community, dashboard):
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, '平均視聴')
            self.assertContains(response, '2:12')

    def test_staff_and_superuser_see_average(self, _today):
        staff = make_user(user_name='watch-staff', email='watch-staff@example.com')
        admin = make_user(user_name='watch-admin', email='watch-admin@example.com', is_superuser=True)
        make_community_member(self.community, staff)
        self._watch()
        for user in (staff, admin):
            with self.subTest(user=user.pk):
                self.client.force_login(user)
                for url in (self.detail_url, self.community_url, self.dashboard_url):
                    self.assertContains(self.client.get(url), '平均視聴')

    def test_presenter_cannot_receive_average_context(self, _today):
        self._watch()
        self.client.force_login(self.presenter)
        with patch('event.views.detail.get_video_watch_times') as watch:
            response = self.client.get(self.detail_url)
        self.assertNotIn('analytics_average_view_duration', response.context)
        self.assertNotContains(response, '平均視聴')
        watch.assert_not_called()
        self.assertNotContains(self.client.get(self.community_url), '平均視聴')
        self.assertNotContains(self.client.get(self.dashboard_url), '平均視聴')

    def test_no_data_preserves_absent_average_in_all_three_places(self, _today):
        self.client.force_login(self.owner)
        detail = self.client.get(self.detail_url)
        self.assertNotIn('analytics_average_view_duration', detail.context)
        for response in (detail, self.client.get(self.community_url), self.client.get(self.dashboard_url)):
            self.assertNotContains(response, '平均視聴')

    def test_zero_views_omit_average(self, _today):
        make_youtube_video_analytics(date=TARGET, views=0)
        self.client.force_login(self.owner)
        for url in (self.detail_url, self.community_url, self.dashboard_url):
            self.assertNotContains(self.client.get(url), '平均視聴')

    def test_zero_seconds_with_views_is_displayed(self, _today):
        make_youtube_video_analytics(date=TARGET, views=10, estimated_minutes_watched=0)
        self.client.force_login(self.owner)
        for url in (self.detail_url, self.community_url, self.dashboard_url):
            self.assertContains(self.client.get(url), '平均視聴')
            self.assertContains(self.client.get(url), '0:00')

    def test_analytics_only_data_shows_cards(self, _today):
        PageAnalytics.objects.all().delete()
        self._watch()
        self.client.force_login(self.owner)
        for url in (self.detail_url, self.community_url):
            response = self.client.get(url)
            self.assertContains(response, '平均視聴')
            self.assertNotContains(response, 'まだ集計できるアクセスがありません')

    def test_period_and_community_filters_preserve_access_boundary(self, _today):
        self._watch()
        make_youtube_video_analytics(date=TODAY - timedelta(days=8), views=10,
                                     estimated_minutes_watched=50)
        other = make_community(name='権限外集会')
        other_detail = make_event_detail(make_event(other, event_date=TODAY), status='approved',
                                         youtube_url='https://youtu.be/12345678901')
        make_youtube_video_analytics(video_id='12345678901', event_detail=other_detail,
                                     date=TARGET, views=1, estimated_minutes_watched=1000)
        self.client.force_login(self.owner)
        response = self.client.get(self.dashboard_url, {'days': 7, 'community': other.pk})
        self.assertEqual(response.context['average_watch_time_by_detail'], {self.detail.pk: 132})
        self.assertNotContains(response, '16:40:00')
        response = self.client.get(self.dashboard_url, {'days': 30})
        self.assertEqual(response.context['average_watch_time_by_detail'], {self.detail.pk: 216})
        self.assertEqual(self.client.get(self.community_url).context['community_analytics']['average_watch_time'], 216)
        self.assertEqual(self.client.get(self.detail_url).context['analytics_average_view_duration'], 216)
