"""自動生成起動ガードとDB再接続のテスト。"""

import datetime
from django.db import OperationalError
from unittest.mock import MagicMock, patch

from django.test import TestCase, tag

from community.models import Community
from twitter.models import TweetQueue


@tag('offline_external_api')
class TweetGenerationThreadGuardTest(TestCase):
    """テスト実行時の本文生成スレッド起動ガードを検証する。"""

    @patch("twitter.services.tweet_generation.threading.Thread.start")
    def test_testing_mode_saves_generation_token_without_starting_thread(self, mock_start):
        """manage.py test では generation_token を保存しつつスレッドを起動しない。"""
        community = Community.objects.create(
            name="Thread Guard Community",
            start_time=datetime.time(22, 0),
            duration=60,
            weekdays=["Mon"],
            frequency="毎週",
            organizers="Test Organizer",
            status="pending",
        )
        queue = TweetQueue.objects.create(
            tweet_type="new_community",
            community=community,
            status="generating",
        )

        from twitter.services import tweet_generation

        with patch.object(tweet_generation.sys, "argv", ["manage.py", "test"]):
            tweet_generation.start_tweet_generation(queue)

        queue.refresh_from_db()
        self.assertTrue(queue.generation_token)
        mock_start.assert_not_called()


@tag('offline_external_api')
class DBReconnectHelperTest(TestCase):
    """MySQL 接続断の再試行ヘルパーのテスト"""

    @patch("twitter.db.connections.close_all")
    def test_run_with_db_reconnect_retries_mysql_lost_connection_once(self, mock_close_all):
        """MySQL 2013 は接続を閉じ直して一度だけ再試行する"""
        from twitter.db import run_with_db_reconnect

        operation = MagicMock(
            side_effect=[
                OperationalError(2013, "Lost connection to server during query"),
                "ok",
            ],
        )

        result = run_with_db_reconnect(operation, context="test operation")

        self.assertEqual(result, "ok")
        self.assertEqual(operation.call_count, 2)
        mock_close_all.assert_called_once()

    @patch("twitter.db.connections.close_all")
    def test_run_with_db_reconnect_does_not_retry_other_operational_error(self, mock_close_all):
        """接続断以外の OperationalError はそのまま呼び出し元へ返す"""
        from twitter.db import run_with_db_reconnect

        operation = MagicMock(side_effect=OperationalError(1048, "Column cannot be null"))

        with self.assertRaises(OperationalError):
            run_with_db_reconnect(operation, context="test operation")

        operation.assert_called_once()
        mock_close_all.assert_not_called()
