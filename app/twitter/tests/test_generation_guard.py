"""自動生成起動ガードとDB再接続のテスト。"""

import datetime
from django.db import OperationalError
from unittest.mock import MagicMock, Mock, patch

from django.test import TestCase, tag

from community.models import Community
from twitter import signals
from twitter.models import TweetQueue
from twitter.services import daily_reminder

@tag('offline_external_api')
class EventTestPatchScopeTest(TestCase):
    """event.tests import が twitter signal を汚染しないことを確認する。"""

    def test_event_tests_import_does_not_patch_tweet_generation_globally(self):
        """event.tests の import だけでは本文生成起動関数をモックしない。"""
        import event.tests  # noqa: F401
        from twitter.services import tweet_generation

        self.assertNotIsInstance(tweet_generation._start_tweet_generation, Mock)
        # 呼び出し側（signals / daily_reminder）も素の関数を参照している
        self.assertNotIsInstance(signals.tweet_generation._start_tweet_generation, Mock)
        self.assertNotIsInstance(
            daily_reminder.tweet_generation._start_tweet_generation, Mock,
        )

    def test_tweet_generation_patch_mixin_scopes_patch_to_class_lifecycle(self):
        """TweetGenerationPatchMixin が setUpClass〜tearDownClass の間だけモック化することを検証する。

        定義元モジュールだけでなく、呼び出し側 (twitter.signals /
        twitter.services.daily_reminder) が参照する関数まで patch が届くことを確認する。
        値渡し import に戻ると呼び出し側は素の関数を掴んだままになり、このテストが落ちる。
        """
        from event.tests.tweet_generation import TweetGenerationPatchMixin
        from twitter.services import tweet_generation

        original = tweet_generation._start_tweet_generation

        class _DummyCase(TweetGenerationPatchMixin, TestCase):
            pass

        # setUpClass 実行前は素のシグナルが残っている
        self.assertIs(tweet_generation._start_tweet_generation, original)
        self.assertIs(signals.tweet_generation._start_tweet_generation, original)
        self.assertIs(daily_reminder.tweet_generation._start_tweet_generation, original)

        _DummyCase.setUpClass()
        try:
            # mixin 適用中はモック化される
            mock_target = tweet_generation._start_tweet_generation
            self.assertIsNot(mock_target, original)
            self.assertIsInstance(mock_target, Mock)
            # 呼び出し側から見えている関数も同じ Mock であること（= patch が届く）
            self.assertIs(signals.tweet_generation._start_tweet_generation, mock_target)
            self.assertIs(
                daily_reminder.tweet_generation._start_tweet_generation, mock_target,
            )
        finally:
            _DummyCase.tearDownClass()

        # tearDownClass 後は元の関数に戻る
        self.assertIs(tweet_generation._start_tweet_generation, original)
        self.assertIs(signals.tweet_generation._start_tweet_generation, original)
        self.assertIs(daily_reminder.tweet_generation._start_tweet_generation, original)

    def test_tweet_generation_patch_mixin_intercepts_signal_call(self):
        """mixin 適用下では signal 経由の本文生成起動が Mock に到達する。"""
        from event.tests.tweet_generation import TweetGenerationPatchMixin

        class _DummyCase(TweetGenerationPatchMixin, TestCase):
            pass

        _DummyCase.setUpClass()
        try:
            mock_start = signals.tweet_generation._start_tweet_generation
            mock_start.reset_mock()
            community = Community.objects.create(
                name="Patch Reach Community",
                start_time=datetime.time(22, 0),
                duration=60,
                weekdays=["Mon"],
                frequency="毎週",
                organizers="Test Organizer",
                status="pending",
            )
            community.status = "approved"
            community.save()

            queue = TweetQueue.objects.get(
                community=community, tweet_type="new_community",
            )
            mock_start.assert_called_once_with(queue)
        finally:
            _DummyCase.tearDownClass()


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
            tweet_generation._start_tweet_generation(queue)

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
