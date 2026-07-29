"""予約投稿エンドポイントの基本動作テスト。"""

import datetime
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from event.models import Event, EventDetail
from twitter.models import TweetQueue

from twitter.tests._auto_tweet_test_base import AutoTweetTestBase

class PostScheduledTweetsViewTest(AutoTweetTestBase):
    """スケジュール投稿エンドポイントのテスト"""

    REQUEST_TOKEN_ENV = {"REQUEST_TOKEN": "test-token"}

    def test_post_scheduled_tweets_unauthorized(self):
        """認証なしで 401 が返る"""
        url = reverse("twitter:post_scheduled_tweets")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 401)

    def test_post_scheduled_tweets_wrong_token(self):
        """不正なトークンで 401 が返る"""
        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(url, HTTP_REQUEST_TOKEN="wrong-token")
            self.assertEqual(response.status_code, 401)

    @patch("twitter.views.post_tweet")
    def test_post_scheduled_tweets_success(self, mock_post):
        """ready 状態のキューが正常に投稿される"""
        mock_post.return_value = {"ok": True, "data": {"id": "12345", "text": "新しい集会の告知テスト"}, "status_code": None, "error_body": None}

        TweetQueue.objects.create(
            tweet_type="new_community",
            community=self.community,
            event=self.event,
            status="ready",
            generated_text="新しい集会の告知テスト",
            scheduled_at=self.due_scheduled_at(),
        )

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(
                url, HTTP_REQUEST_TOKEN="test-token",
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["processed"], 1)
        self.assertEqual(data["results"][0]["status"], "posted")

        # DB の状態確認
        queue = TweetQueue.objects.first()
        self.assertEqual(queue.status, "posted")
        self.assertEqual(queue.tweet_id, "12345")
        self.assertIsNotNone(queue.posted_at)

    @patch("twitter.views.post_tweet")
    def test_post_scheduled_tweets_posts_only_one_ready_queue_per_request(self, mock_post):
        """複数件 ready があっても1回の実行で投稿するのは1件だけ"""
        mock_post.return_value = {"ok": True, "data": {"id": "first-post", "text": "1件目"}, "status_code": None, "error_body": None}

        first = TweetQueue.objects.create(
            tweet_type="new_community",
            community=self.community,
            event=self.event,
            status="ready",
            generated_text="1件目",
            scheduled_at=self.due_scheduled_at() - datetime.timedelta(minutes=2),
        )
        second = TweetQueue.objects.create(
            tweet_type="lt",
            community=self.community,
            event=self.event,
            status="ready",
            generated_text="2件目",
            scheduled_at=self.due_scheduled_at() - datetime.timedelta(minutes=1),
        )

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(url, HTTP_REQUEST_TOKEN="test-token")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["processed"], 1)
        self.assertTrue(data["posted_attempted"])
        mock_post.assert_called_once_with("1件目", media_ids=None)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, "posted")
        self.assertEqual(second.status, "ready")

    @override_settings(DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/test/token")
    @patch("twitter.notifications.post_discord_webhook")
    @patch("twitter.views.post_tweet")
    def test_post_scheduled_tweets_post_failure(self, mock_post, mock_webhook_post):
        """X API 投稿失敗時にキューが failed になり管理者へ通知が送られる"""
        mock_post.return_value = {"ok": False, "data": None, "status_code": 403, "error_body": "You are not permitted to perform this action."}
        mock_webhook_post.return_value = MagicMock(status_code=204)

        TweetQueue.objects.create(
            tweet_type="new_community",
            community=self.community,
            event=self.event,
            status="ready",
            generated_text="テストツイート",
            scheduled_at=self.due_scheduled_at(),
        )

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(
                url, HTTP_REQUEST_TOKEN="test-token",
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["results"][0]["error"], "post_failed")

        queue = TweetQueue.objects.first()
        self.assertEqual(queue.status, "failed")

        mock_webhook_post.assert_called_once()
        self.assertEqual(
            mock_webhook_post.call_args.args[0],
            settings.DISCORD_WEBHOOK_URL,
        )

    def test_post_scheduled_tweets_empty_queue(self):
        """キューが空の場合の処理"""
        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(
                url, HTTP_REQUEST_TOKEN="test-token",
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["processed"], 0)
        self.assertEqual(data["results"], [])
        self.assertEqual(data["retried"], 0)

    @patch("twitter.views.post_tweet")
    def test_post_scheduled_tweets_posts_existing_daily_reminder_for_today_event(self, mock_post):
        """当日リマインドは事前作成済みキューをそのまま投稿する"""
        mock_post.return_value = {"ok": True, "data": {"id": "dr-123", "text": "今日開催のリマインド"}, "status_code": None, "error_body": None}

        today_event = Event.objects.create(
            community=self.community,
            date=timezone.localdate(),
            start_time=datetime.time(21, 0),
            duration=60,
        )
        TweetQueue.objects.create(
            tweet_type="daily_reminder",
            community=self.community,
            event=today_event,
            status="ready",
            generated_text="今日開催のリマインド",
            scheduled_at=self.due_scheduled_at(),
        )

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(url, HTTP_REQUEST_TOKEN="test-token")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["created"], 0)
        self.assertEqual(data["processed"], 1)

        queue = TweetQueue.objects.get(tweet_type="daily_reminder")
        self.assertEqual(queue.event, today_event)
        self.assertEqual(queue.status, "posted")
        self.assertEqual(queue.generated_text, "今日開催のリマインド")

    @patch("twitter.views.post_tweet")
    def test_post_scheduled_tweets_skips_same_day_individual_queue(self, mock_post):
        """当日の個別 LT キューが残っていても投稿せず skipped に補正する"""
        today_event = Event.objects.create(
            community=self.community,
            date=timezone.localdate(),
            start_time=datetime.time(21, 0),
            duration=60,
        )
        queue = TweetQueue.objects.create(
            tweet_type="lt",
            community=self.community,
            event=today_event,
            status="ready",
            generated_text="今日の個別LT告知",
            scheduled_at=self.due_scheduled_at(),
        )

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(url, HTTP_REQUEST_TOKEN="test-token")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["created"], 0)
        self.assertEqual(data["results"][0]["status"], "skipped")
        queue.refresh_from_db()
        self.assertEqual(queue.status, "skipped")
        self.assertEqual(queue.generated_text, "")
        mock_post.assert_not_called()

    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_post_scheduled_tweets_ignores_non_approved_or_non_lt_details(self, mock_thread_cls):
        """approved な LT/SPECIAL がなくてもスケジューラは新規キューを作らない"""
        mock_thread_cls.return_value = MagicMock()

        pending_event = Event.objects.create(
            community=self.community,
            date=timezone.localdate(),
            start_time=datetime.time(20, 0),
            duration=60,
        )
        blog_event = Event.objects.create(
            community=self.community,
            date=timezone.localdate(),
            start_time=datetime.time(22, 0),
            duration=60,
        )
        EventDetail.objects.create(
            event=pending_event,
            detail_type="LT",
            status="pending",
            speaker="保留太郎",
            theme="未承認LT",
            start_time=datetime.time(20, 15),
        )
        EventDetail.objects.create(
            event=blog_event,
            detail_type="BLOG",
            status="approved",
            speaker="ブロガー",
            theme="ブログ記事",
            start_time=datetime.time(22, 15),
        )
        TweetQueue.objects.all().delete()

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(url, HTTP_REQUEST_TOKEN="test-token")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["created"], 0)
        self.assertFalse(TweetQueue.objects.filter(tweet_type="daily_reminder").exists())

    @patch("twitter.views.post_tweet")
    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_post_scheduled_tweets_does_not_create_missing_daily_reminder(self, mock_thread_cls, mock_post):
        """daily_reminder が未作成ならスケジューラは補完作成しない"""
        mock_thread_cls.return_value = MagicMock()

        today_event = Event.objects.create(
            community=self.community,
            date=timezone.localdate(),
            start_time=datetime.time(21, 0),
            duration=60,
        )
        EventDetail.objects.create(
            event=today_event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="今日の発表",
            start_time=datetime.time(21, 15),
        )
        TweetQueue.objects.all().delete()

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(url, HTTP_REQUEST_TOKEN="test-token")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["created"], 0)
        self.assertFalse(TweetQueue.objects.filter(tweet_type="daily_reminder").exists())
        mock_post.assert_not_called()

    @patch("twitter.views.post_tweet")
    def test_post_scheduled_tweets_with_pregenerated_text(self, mock_post):
        """ready 状態で事前テキストがある場合はそのまま投稿"""
        mock_post.return_value = {"ok": True, "data": {"id": "99999", "text": "事前生成テキスト"}, "status_code": None, "error_body": None}

        TweetQueue.objects.create(
            tweet_type="new_community",
            community=self.community,
            event=self.event,
            status="ready",
            generated_text="事前生成テキスト",
            scheduled_at=self.due_scheduled_at(),
        )

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(
                url, HTTP_REQUEST_TOKEN="test-token",
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["results"][0]["status"], "posted")
