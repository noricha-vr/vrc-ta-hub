"""動画統計の冪等同期と、累計・期間差分の集計テスト。"""
from datetime import date, time, timedelta
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from analytics.models import PageAnalytics, YouTubeVideoStats
from analytics.video_stats import get_video_views, sync_youtube_stats
from community.models import Community
from event.models import Event, EventDetail

FIXED_TODAY = date(2026, 10, 23)
VIDEO_ID = 'abcdefghijk'
OTHER_VIDEO_ID = 'lmnopqrstuv'
TEST_TOKEN = 'test-request-token'


class VideoStatsTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.community = Community.objects.create(
            name='動画統計テスト集会', frequency='毎週', organizers='主催',
        )
        cls.event = Event.objects.create(
            community=cls.community, date=FIXED_TODAY, start_time=time(22),
            duration=60, weekday='Fri',
        )
        cls.detail = EventDetail.objects.create(
            event=cls.event, youtube_url=f'https://www.youtube.com/watch?v={VIDEO_ID}',
        )
        cls.shared_detail = EventDetail.objects.create(
            event=cls.event, youtube_url=f'https://youtu.be/{VIDEO_ID}?t=30',
        )

    def _snapshot(self, target_date, count, *, video_id=VIDEO_ID, detail=None):
        return YouTubeVideoStats.objects.create(
            video_id=video_id, event_detail=detail or self.detail,
            date=target_date, view_count=count,
        )


class YouTubeVideoStatsModelTest(VideoStatsTestCase):
    def test_optional_fields_and_large_counts(self):
        row = YouTubeVideoStats.objects.create(
            video_id=VIDEO_ID, date=FIXED_TODAY, view_count=2 ** 40,
        )
        row.refresh_from_db()
        self.assertIsNone(row.event_detail)
        self.assertIsNone(row.like_count)
        self.assertIsNone(row.comment_count)
        self.assertIsNotNone(row.created_at)
        self.assertEqual(row.view_count, 2 ** 40)

    def test_video_and_date_are_unique_even_for_different_presentations(self):
        self._snapshot(FIXED_TODAY, 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._snapshot(FIXED_TODAY, 2, detail=self.shared_detail)

    def test_event_deletion_cascades_to_statistics(self):
        self._snapshot(FIXED_TODAY, 1)
        self.assertEqual(self.detail.youtube_stats.count(), 1)
        self.event.delete()
        self.assertFalse(YouTubeVideoStats.objects.exists())


class SyncYouTubeStatsTest(VideoStatsTestCase):
    @patch('analytics.video_stats.fetch_video_statistics')
    def test_sync_is_idempotent_and_shared_video_uses_first_presentation(self, mock_fetch):
        target_date = FIXED_TODAY - timedelta(days=1)
        mock_fetch.return_value = {
            VIDEO_ID: {'view_count': 10, 'like_count': 2, 'comment_count': None},
        }
        self.assertEqual(sync_youtube_stats(target_date), 1)
        row = YouTubeVideoStats.objects.get()
        created_at = row.created_at
        self.assertEqual(row.date, target_date)
        self.assertEqual(row.event_detail, self.detail)
        self.assertEqual(row.like_count, 2)
        self.assertIsNone(row.comment_count)
        mock_fetch.assert_called_once_with([VIDEO_ID])

        mock_fetch.return_value[VIDEO_ID]['view_count'] = 25
        self.assertEqual(sync_youtube_stats(target_date), 1)
        self.assertEqual(YouTubeVideoStats.objects.count(), 1)
        row.refresh_from_db()
        self.assertEqual(row.view_count, 25)
        self.assertEqual(row.created_at, created_at)

        self.assertEqual(sync_youtube_stats(FIXED_TODAY), 1)
        self.assertEqual(YouTubeVideoStats.objects.count(), 2)

    @patch('analytics.video_stats.fetch_video_statistics', return_value={})
    def test_missing_api_results_do_not_create_or_erase_snapshots(self, mock_fetch):
        row = self._snapshot(FIXED_TODAY, 99)
        self.assertEqual(sync_youtube_stats(FIXED_TODAY), 0)
        row.refresh_from_db()
        self.assertEqual(row.view_count, 99)
        self.assertEqual(YouTubeVideoStats.objects.count(), 1)

    @patch('analytics.video_stats.fetch_video_statistics', return_value={})
    def test_deleted_empty_and_non_video_urls_are_excluded(self, mock_fetch):
        for url in (None, '', 'https://discord.com/channels/123/456/789',
                    'https://www.youtube.com/playlist?list=123'):
            EventDetail.objects.create(event=self.event, youtube_url=url)
        EventDetail.objects.create(
            event=self.event, youtube_url=f'https://youtu.be/{OTHER_VIDEO_ID}',
            deleted_at=timezone.now(),
        )
        sync_youtube_stats(FIXED_TODAY)
        mock_fetch.assert_called_once_with([VIDEO_ID])

    @patch('analytics.video_stats.fetch_video_statistics')
    def test_no_videos_skips_api(self, mock_fetch):
        EventDetail.objects.all().update(youtube_url='')
        self.assertEqual(sync_youtube_stats(FIXED_TODAY), 0)
        mock_fetch.assert_not_called()


@patch('analytics.video_stats.timezone.localdate', return_value=FIXED_TODAY)
class GetVideoViewsTest(VideoStatsTestCase):
    def test_period_uses_both_boundaries_and_total_uses_latest_snapshot(self, _mock_today):
        self._snapshot(FIXED_TODAY - timedelta(days=32), 1)
        self._snapshot(FIXED_TODAY - timedelta(days=31), 100)
        self._snapshot(FIXED_TODAY - timedelta(days=1), 160)
        self._snapshot(FIXED_TODAY, 200)

        with self.assertNumQueries(2):
            result = get_video_views([self.detail.pk, self.shared_detail.pk])
        self.assertEqual(result, {
            self.detail.pk: {'total': 200, 'period': 60},
            self.shared_detail.pk: {'total': 200, 'period': 60},
        })

    def test_custom_period(self, _mock_today):
        self._snapshot(FIXED_TODAY - timedelta(days=8), 50)
        self._snapshot(FIXED_TODAY - timedelta(days=1), 70)
        self.assertEqual(get_video_views([self.detail.pk], days=7)[self.detail.pk], {
            'total': 70, 'period': 20,
        })

    def test_missing_start_returns_none_instead_of_assuming_zero(self, _mock_today):
        self._snapshot(FIXED_TODAY - timedelta(days=30), 100)
        self._snapshot(FIXED_TODAY - timedelta(days=1), 160)
        self.assertEqual(get_video_views([self.detail.pk])[self.detail.pk], {
            'total': 160, 'period': None,
        })

    def test_missing_end_returns_none(self, _mock_today):
        self._snapshot(FIXED_TODAY - timedelta(days=31), 100)
        self._snapshot(FIXED_TODAY - timedelta(days=2), 160)
        self._snapshot(FIXED_TODAY, 200)
        self.assertEqual(get_video_views([self.detail.pk])[self.detail.pk], {
            'total': 200, 'period': None,
        })

    def test_negative_difference_is_clamped_to_zero(self, _mock_today):
        self._snapshot(FIXED_TODAY - timedelta(days=31), 200)
        self._snapshot(FIXED_TODAY - timedelta(days=1), 160)
        self.assertEqual(get_video_views([self.detail.pk])[self.detail.pk], {
            'total': 160, 'period': 0,
        })

    def test_unavailable_view_count_returns_none(self, _mock_today):
        self._snapshot(FIXED_TODAY - timedelta(days=31), 100)
        self._snapshot(FIXED_TODAY - timedelta(days=1), None)
        self.assertEqual(get_video_views([self.detail.pk])[self.detail.pk], {
            'total': None, 'period': None,
        })

    def test_unavailable_start_count_returns_none(self, _mock_today):
        self._snapshot(FIXED_TODAY - timedelta(days=31), None)
        self._snapshot(FIXED_TODAY - timedelta(days=1), 160)
        self.assertEqual(get_video_views([self.detail.pk])[self.detail.pk], {
            'total': 160, 'period': None,
        })

    def test_no_snapshot_returns_none_counts(self, _mock_today):
        self.assertEqual(get_video_views([self.detail.pk]), {
            self.detail.pk: {'total': None, 'period': None},
        })

    def test_only_requested_active_presentations_with_video_are_returned(self, _mock_today):
        other = EventDetail.objects.create(
            event=self.event, youtube_url=f'https://youtu.be/{OTHER_VIDEO_ID}',
        )
        self._snapshot(FIXED_TODAY, 999, video_id=OTHER_VIDEO_ID, detail=other)
        deleted = EventDetail.objects.create(
            event=self.event, youtube_url=f'https://youtu.be/{VIDEO_ID}',
            deleted_at=timezone.now(),
        )
        invalid = EventDetail.objects.create(event=self.event, youtube_url='https://example.com/')
        self.assertEqual(get_video_views([self.detail.pk, deleted.pk, invalid.pk, 99999999]), {
            self.detail.pk: {'total': None, 'period': None},
        })

    def test_empty_ids_do_not_query(self, _mock_today):
        with self.assertNumQueries(0):
            self.assertEqual(get_video_views([]), {})


@override_settings(REQUEST_TOKEN=TEST_TOKEN, GA4_PROPERTY_ID='123456789')
class SyncAnalyticsYouTubeTest(VideoStatsTestCase):
    def setUp(self):
        for name, value in (
            ('fetch_page_report', []), ('fetch_poster_click_report', []),
        ):
            patcher = patch(f'analytics.views.{name}', return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.url = reverse('analytics:sync')

    @patch('analytics.views.sync_youtube_stats', return_value=1)
    def test_success_reports_count_and_passes_target_date(self, mock_sync):
        response = self.client.get(
            self.url, {'date': '2026-10-22'}, HTTP_REQUEST_TOKEN=TEST_TOKEN,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('YouTube: 1', response.content.decode())
        mock_sync.assert_called_once_with(date(2026, 10, 22))

    @patch('analytics.video_stats.fetch_video_statistics', side_effect=RuntimeError('YouTube failed'))
    def test_youtube_failure_preserves_successful_ga4_sync(self, mock_fetch):
        with patch('analytics.views.fetch_page_report', return_value=[{
            'page_path': '/', 'date': '2026-10-22', 'source_medium': '(direct) / (none)',
            'campaign': '(not set)', 'pv': 10, 'users': 8, 'sessions': 9,
        }]), self.assertLogs('analytics', level='ERROR') as logs:
            response = self.client.get(
                self.url, {'date': '2026-10-22'}, HTTP_REQUEST_TOKEN=TEST_TOKEN,
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn('YouTube: failed', response.content.decode())
        self.assertEqual(PageAnalytics.objects.get().pv, 10)
        self.assertFalse(YouTubeVideoStats.objects.exists())
        mock_fetch.assert_called_once_with([VIDEO_ID])
        self.assertIsNotNone(logs.records[0].exc_info)

    @patch('analytics.views.sync_youtube_stats')
    def test_ga4_failure_does_not_run_youtube_sync(self, mock_sync):
        with patch('analytics.views.fetch_page_report', side_effect=RuntimeError('GA4 failed')):
            response = self.client.get(self.url, HTTP_REQUEST_TOKEN=TEST_TOKEN)
        self.assertEqual(response.status_code, 500)
        mock_sync.assert_not_called()
