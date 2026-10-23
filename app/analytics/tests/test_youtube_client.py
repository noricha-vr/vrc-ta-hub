"""YouTube 公開統計の取得を実 API に接続せずに検証する。"""
import logging
from unittest.mock import call, patch

from django.test import SimpleTestCase, override_settings
from googleapiclient.errors import HttpError
from httplib2 import Response

from analytics.youtube_client import fetch_video_statistics


@override_settings(GOOGLE_API_KEY='test-api-key')
class FetchVideoStatisticsTest(SimpleTestCase):
    @patch('analytics.youtube_client.build')
    def test_requests_are_split_into_batches_of_50(self, mock_build):
        video_ids = [f'video{i:06d}' for i in range(101)]
        videos = mock_build.return_value.videos.return_value
        videos.list.return_value.execute.side_effect = [
            {'items': [
                {'id': video_id, 'statistics': {
                    'viewCount': '123', 'likeCount': '4', 'commentCount': '0',
                }}
                for video_id in video_ids[offset:offset + 50]
            ]}
            for offset in range(0, 101, 50)
        ]

        result = fetch_video_statistics(video_ids)

        mock_build.assert_called_once_with('youtube', 'v3', developerKey='test-api-key')
        self.assertEqual(videos.list.call_args_list, [
            call(part='statistics', id=','.join(video_ids[offset:offset + 50]))
            for offset in range(0, 101, 50)
        ])
        self.assertEqual(len(result), 101)
        self.assertEqual(result[video_ids[-1]], {
            'view_count': 123, 'like_count': 4, 'comment_count': 0,
        })

    @patch('analytics.youtube_client.build')
    def test_missing_statistics_are_none_and_missing_videos_are_omitted(self, mock_build):
        mock_build.return_value.videos.return_value.list.return_value.execute.return_value = {
            'items': [
                {'id': 'video000001', 'statistics': {'viewCount': '0'}},
                {'id': 'video000002'},
            ],
        }

        result = fetch_video_statistics(['video000001', 'video000002', 'video000003'])

        self.assertEqual(result, {
            'video000001': {'view_count': 0, 'like_count': None, 'comment_count': None},
            'video000002': {'view_count': None, 'like_count': None, 'comment_count': None},
        })

    @patch('analytics.youtube_client.build')
    def test_duplicate_ids_are_requested_once(self, mock_build):
        mock_build.return_value.videos.return_value.list.return_value.execute.return_value = {}
        self.assertEqual(fetch_video_statistics(['video000001', 'video000001']), {})
        mock_build.return_value.videos.return_value.list.assert_called_once_with(
            part='statistics', id='video000001',
        )

    @override_settings(GOOGLE_API_KEY='')
    @patch('analytics.youtube_client.build')
    def test_missing_api_key_logs_and_skips_request(self, mock_build):
        with self.assertLogs('analytics', level='WARNING'):
            self.assertEqual(fetch_video_statistics(['video000001']), {})
        mock_build.assert_not_called()

    @patch('analytics.youtube_client.build')
    def test_empty_ids_do_not_build_client(self, mock_build):
        self.assertEqual(fetch_video_statistics([]), {})
        mock_build.assert_not_called()

    @patch('analytics.youtube_client.build')
    def test_api_failure_propagates_to_sync_caller(self, mock_build):
        mock_build.return_value.videos.return_value.list.return_value.execute.side_effect = RuntimeError('API failed')
        with self.assertRaises(RuntimeError):
            fetch_video_statistics(['video000001'])

    @patch('analytics.youtube_client.build')
    def test_http_failure_log_does_not_include_api_key(self, mock_build):
        mock_build.return_value.videos.return_value.list.return_value.execute.side_effect = HttpError(
            Response({'status': '403'}), b'{"error": {"message": "quota exceeded"}}',
            uri='https://www.googleapis.com/youtube/v3/videos?key=test-api-key',
        )
        with self.assertLogs('analytics', level='ERROR') as logs:
            try:
                fetch_video_statistics(['video000001'])
            except RuntimeError:
                logging.getLogger('analytics').exception('YouTube statistics sync failed')

        self.assertIn('HTTP 403', logs.output[0])
        self.assertNotIn('test-api-key', logs.output[0])
