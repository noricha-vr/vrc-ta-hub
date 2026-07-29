"""予約投稿の期限判定と生成再試行のテスト。"""

import datetime
from unittest.mock import MagicMock, patch

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from community.models import Community
from event.models import Event
from twitter.models import TweetQueue

from twitter.tests._auto_tweet_test_base import AutoTweetTestBase

class PostScheduledTweetsExpiredEventTest(AutoTweetTestBase):
    """投稿時のイベント日チェックテスト"""

    REQUEST_TOKEN_ENV = {"REQUEST_TOKEN": "test-token"}

    def setUp(self):
        super().setUp()
        with patch("twitter.services.tweet_generation.threading.Thread") as mock_thread_cls:
            mock_thread_cls.return_value = MagicMock()
            self.community.status = "approved"
            self.community.save()
        TweetQueue.objects.all().delete()

    @patch("twitter.views.post_tweet")
    def test_expired_lt_tweet_is_skipped(self, mock_post):
        """イベント日が過去のLTツイートは投稿されずスキップされる"""
        past_event = Event.objects.create(
            community=self.community,
            date=datetime.date(2025, 1, 1),
            start_time=datetime.time(22, 0),
            duration=60,
        )
        TweetQueue.objects.create(
            tweet_type="lt",
            community=self.community,
            event=past_event,
            status="ready",
            generated_text="過去のLT告知",
            scheduled_at=self.due_scheduled_at(),
        )

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(url, HTTP_REQUEST_TOKEN="test-token")

        self.assertEqual(response.status_code, 200)
        queue = TweetQueue.objects.first()
        self.assertEqual(queue.status, "failed")
        self.assertIn("過去", queue.error_message)
        mock_post.assert_not_called()

    @patch("twitter.views.post_tweet")
    def test_future_lt_tweet_is_posted(self, mock_post):
        """未来のイベントのLTツイートは通常通り投稿される"""
        mock_post.return_value = {"ok": True, "data": {"id": "99999", "text": "未来のLT告知"}, "status_code": None, "error_body": None}

        TweetQueue.objects.create(
            tweet_type="lt",
            community=self.community,
            event=self.event,  # 2099-05-01（未来）
            status="ready",
            generated_text="未来のLT告知",
            scheduled_at=self.due_scheduled_at(),
        )

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(url, HTTP_REQUEST_TOKEN="test-token")

        self.assertEqual(response.status_code, 200)
        queue = TweetQueue.objects.first()
        self.assertEqual(queue.status, "posted")

    @patch("twitter.views.post_tweet")
    def test_expired_special_tweet_is_skipped(self, mock_post):
        """イベント日が過去の特別回ツイートもスキップされる"""
        past_event = Event.objects.create(
            community=self.community,
            date=datetime.date(2025, 1, 1),
            start_time=datetime.time(22, 0),
            duration=60,
        )
        TweetQueue.objects.create(
            tweet_type="special",
            community=self.community,
            event=past_event,
            status="ready",
            generated_text="過去の特別回告知",
            scheduled_at=self.due_scheduled_at(),
        )

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            self.client.get(url, HTTP_REQUEST_TOKEN="test-token")

        queue = TweetQueue.objects.first()
        self.assertEqual(queue.status, "failed")
        mock_post.assert_not_called()

    @patch("twitter.views.post_tweet")
    def test_slide_share_is_not_affected_by_date_check(self, mock_post):
        """スライド共有は過去イベントでも投稿される（資料共有は事後）"""
        mock_post.return_value = {"ok": True, "data": {"id": "88888", "text": "スライド共有"}, "status_code": None, "error_body": None}

        past_event = Event.objects.create(
            community=self.community,
            date=datetime.date(2025, 1, 1),
            start_time=datetime.time(22, 0),
            duration=60,
        )
        TweetQueue.objects.create(
            tweet_type="slide_share",
            community=self.community,
            event=past_event,
            status="ready",
            generated_text="スライド共有",
            scheduled_at=self.due_scheduled_at(),
        )

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            self.client.get(url, HTTP_REQUEST_TOKEN="test-token")

        queue = TweetQueue.objects.first()
        self.assertEqual(queue.status, "posted")

    @patch("twitter.views.post_tweet")
    def test_stale_daily_reminder_is_skipped(self, mock_post):
        """当日ではない daily_reminder は投稿しない"""
        past_event = Event.objects.create(
            community=self.community,
            date=timezone.localdate() - datetime.timedelta(days=1),
            start_time=datetime.time(22, 0),
            duration=60,
        )
        TweetQueue.objects.create(
            tweet_type="daily_reminder",
            community=self.community,
            event=past_event,
            status="ready",
            generated_text="昨日開催のリマインド",
            scheduled_at=self.due_scheduled_at(),
        )

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(url, HTTP_REQUEST_TOKEN="test-token")

        self.assertEqual(response.status_code, 200)
        queue = TweetQueue.objects.first()
        self.assertEqual(queue.status, "failed")
        self.assertIn("当日イベントではない", queue.error_message)
        mock_post.assert_not_called()


class RetryGenerationTest(AutoTweetTestBase):
    """_retry_generation 関数のテスト"""

    @patch("twitter.tweet_generator.generate_new_community_tweet")
    def test_retry_success(self, mock_generate):
        """リトライ成功時に status が ready になる"""
        mock_generate.return_value = "リトライ成功テキスト"

        queue_item = TweetQueue.objects.create(
            tweet_type="new_community",
            community=self.community,
            event=self.event,
            status="generation_failed",
            error_message="前回の失敗",
        )

        from twitter.views import _retry_generation
        _retry_generation(queue_item)

        queue_item.refresh_from_db()
        self.assertEqual(queue_item.status, "ready")
        self.assertEqual(queue_item.generated_text, "リトライ成功テキスト")
        self.assertEqual(queue_item.error_message, "")

    @patch("twitter.tweet_generator.generate_new_community_tweet")
    def test_retry_failure(self, mock_generate):
        """リトライ失敗時に status が generation_failed のまま"""
        mock_generate.return_value = None

        queue_item = TweetQueue.objects.create(
            tweet_type="new_community",
            community=self.community,
            event=self.event,
            status="generation_failed",
        )

        from twitter.views import _retry_generation
        _retry_generation(queue_item)

        queue_item.refresh_from_db()
        self.assertEqual(queue_item.status, "generation_failed")
        self.assertIn("リトライ生成にも失敗", queue_item.error_message)

    @patch("twitter.tweet_generator.generate_new_community_tweet")
    def test_retry_exception_sets_generation_failed(self, mock_generate):
        """リトライ中に例外が発生した場合 generation_failed に更新される"""
        mock_generate.side_effect = RuntimeError("LLM connection timeout")

        queue_item = TweetQueue.objects.create(
            tweet_type="new_community",
            community=self.community,
            event=self.event,
            status="generation_failed",
            error_message="前回の失敗",
        )

        from twitter.views import _retry_generation
        _retry_generation(queue_item)

        queue_item.refresh_from_db()
        self.assertEqual(queue_item.status, "generation_failed")
        self.assertIn("リトライ中に例外が発生", queue_item.error_message)

    @patch("twitter.tweet_generator.generate_new_community_tweet")
    def test_retry_exception_does_not_stop_loop(self, mock_generate):
        """リトライ中の例外が他のアイテム処理を妨げないことを確認"""
        mock_generate.side_effect = [
            RuntimeError("1st item exception"),
            "2番目のアイテムは成功",
        ]

        queue1 = TweetQueue.objects.create(
            tweet_type="new_community",
            community=self.community,
            event=self.event,
            status="generation_failed",
        )
        queue2 = TweetQueue.objects.create(
            tweet_type="new_community",
            community=self.community,
            event=self.event,
            status="generation_failed",
        )

        from twitter.views import _retry_generation
        _retry_generation(queue1)
        _retry_generation(queue2)

        queue1.refresh_from_db()
        queue2.refresh_from_db()
        self.assertEqual(queue1.status, "generation_failed")
        self.assertEqual(queue2.status, "ready")
        self.assertEqual(queue2.generated_text, "2番目のアイテムは成功")

    @override_settings(AWS_S3_CUSTOM_DOMAIN='data.vrc-ta-hub.com')
    @patch("twitter.tweet_generator.generate_new_community_tweet")
    def test_retry_success_sets_image_url(self, mock_generate):
        """リトライ成功時にCF Image Resizing URLが設定される"""
        mock_generate.return_value = "リトライ成功"

        Community.objects.filter(pk=self.community.pk).update(
            poster_image="community/1/poster.webp",
        )
        self.community.refresh_from_db()

        queue_item = TweetQueue.objects.create(
            tweet_type="new_community",
            community=self.community,
            event=self.event,
            status="generation_failed",
        )

        from twitter.views import _retry_generation
        _retry_generation(queue_item)

        queue_item.refresh_from_db()
        self.assertEqual(queue_item.status, "ready")
        self.assertIn("/cdn-cgi/image/width=960", queue_item.image_url)
        self.assertIn("community/1/poster.webp", queue_item.image_url)

    @patch("twitter.views.connections.close_all")
    @patch("twitter.views._retry_generation")
    def test_retry_generation_async_closes_db_connections(self, mock_retry, mock_close_all):
        """手動リトライのバックグラウンド実行後にDB接続を閉じる"""
        queue_item = TweetQueue.objects.create(
            tweet_type="new_community",
            community=self.community,
            event=self.event,
            status="generation_failed",
        )

        from twitter.views import _retry_generation_async
        _retry_generation_async(queue_item.pk)

        mock_retry.assert_called_once()
        self.assertEqual(mock_retry.call_args.args[0].pk, queue_item.pk)
        mock_close_all.assert_called_once()

    @patch("twitter.views.connections.close_all")
    def test_retry_generation_async_closes_db_connections_for_missing_queue(self, mock_close_all):
        """対象キューが存在しない場合もDB接続を閉じる"""
        from twitter.views import _retry_generation_async
        _retry_generation_async(99999)

        mock_close_all.assert_called_once()
