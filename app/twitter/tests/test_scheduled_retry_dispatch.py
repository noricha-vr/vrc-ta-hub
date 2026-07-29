"""予約投稿エンドポイントの再試行と画像処理のテスト。"""

import datetime
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from twitter.models import TweetQueue

from twitter.tests._auto_tweet_test_base import AutoTweetTestBase

class PostScheduledTweetsRetryDispatchTest(AutoTweetTestBase):
    """予約投稿時の生成再試行と画像処理を検証する。"""

    REQUEST_TOKEN_ENV = {"REQUEST_TOKEN": "test-token"}

    @patch("twitter.views.post_tweet")
    @patch("twitter.tweet_generator.generate_new_community_tweet")
    def test_retry_generation_failed_items(self, mock_generate, mock_post):
        """generation_failed のキューがリトライされて投稿される"""
        mock_generate.return_value = "リトライ成功テキスト"
        mock_post.return_value = {"ok": True, "data": {"id": "77777", "text": "リトライ成功テキスト"}, "status_code": None, "error_body": None}

        TweetQueue.objects.create(
            tweet_type="new_community",
            community=self.community,
            event=self.event,
            status="generation_failed",
            error_message="前回の失敗",
            scheduled_at=self.due_scheduled_at(),
        )

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(
                url, HTTP_REQUEST_TOKEN="test-token",
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["retried"], 1)

        # リトライ成功 -> 投稿成功
        queue = TweetQueue.objects.first()
        self.assertEqual(queue.status, "posted")
        self.assertEqual(queue.generated_text, "リトライ成功テキスト")

    @patch("twitter.views.post_tweet")
    @patch("twitter.tweet_generator.generate_new_community_tweet")
    def test_retry_stale_generating_items(self, mock_generate, mock_post):
        """1時間以上前の generating キューがリトライされて投稿される"""
        mock_generate.return_value = "リトライ成功テキスト"
        mock_post.return_value = {"ok": True, "data": {"id": "88888", "text": "リトライ成功テキスト"}, "status_code": None, "error_body": None}

        queue = TweetQueue.objects.create(
            tweet_type="new_community",
            community=self.community,
            event=self.event,
            status="generating",
            scheduled_at=self.due_scheduled_at(),
        )
        # created_at を1時間以上前に更新
        TweetQueue.objects.filter(pk=queue.pk).update(
            created_at=timezone.now() - datetime.timedelta(hours=2),
        )

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(
                url, HTTP_REQUEST_TOKEN="test-token",
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["retried"], 1)

        queue.refresh_from_db()
        self.assertEqual(queue.status, "posted")

    def test_recent_generating_not_retried(self):
        """1時間以内の generating キューはリトライされない"""
        TweetQueue.objects.create(
            tweet_type="new_community",
            community=self.community,
            event=self.event,
            status="generating",
            scheduled_at=self.due_scheduled_at(),
        )

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(
                url, HTTP_REQUEST_TOKEN="test-token",
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["retried"], 0)

        # generating のまま
        queue = TweetQueue.objects.first()
        self.assertEqual(queue.status, "generating")

    @patch("twitter.tweet_generator.generate_new_community_tweet")
    def test_retry_generation_failed_items_before_scheduled_at(self, mock_generate):
        """future の generation_failed キューも前倒しで再生成される"""
        mock_generate.return_value = "未来キューの回復テキスト"

        queue = TweetQueue.objects.create(
            tweet_type="new_community",
            community=self.community,
            event=self.event,
            status="generation_failed",
            scheduled_at=self.future_scheduled_at(),
        )

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(url, HTTP_REQUEST_TOKEN="test-token")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["retried"], 1)
        queue.refresh_from_db()
        self.assertEqual(queue.status, "ready")
        self.assertEqual(queue.generated_text, "未来キューの回復テキスト")

    @patch("twitter.tweet_generator.generate_new_community_tweet")
    def test_retry_stale_generating_before_scheduled_at(self, mock_generate):
        """future の stale generating キューも前倒しで再生成される"""
        mock_generate.return_value = "未来generating回復テキスト"

        queue = TweetQueue.objects.create(
            tweet_type="new_community",
            community=self.community,
            event=self.event,
            status="generating",
            scheduled_at=self.future_scheduled_at(),
        )
        TweetQueue.objects.filter(pk=queue.pk).update(
            created_at=timezone.now() - datetime.timedelta(hours=2),
        )

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(url, HTTP_REQUEST_TOKEN="test-token")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["retried"], 1)
        queue.refresh_from_db()
        self.assertEqual(queue.status, "ready")
        self.assertEqual(queue.generated_text, "未来generating回復テキスト")

    @patch("twitter.views.post_tweet")
    def test_ready_queue_waits_until_scheduled_at(self, mock_post):
        """予約日時前の ready キューは投稿しない"""
        queue = TweetQueue.objects.create(
            tweet_type="new_community",
            community=self.community,
            event=self.event,
            status="ready",
            generated_text="未来の予約投稿",
            scheduled_at=self.future_scheduled_at(),
        )

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(url, HTTP_REQUEST_TOKEN="test-token")

        self.assertEqual(response.status_code, 200)
        queue.refresh_from_db()
        self.assertEqual(queue.status, "ready")
        mock_post.assert_not_called()

    @patch("twitter.views.post_tweet")
    def test_overdue_ready_queue_is_skipped(self, mock_post):
        """予約日時から24時間以上経過した未投稿キューは skipped になる"""
        queue = TweetQueue.objects.create(
            tweet_type="new_community",
            community=self.community,
            event=self.event,
            status="ready",
            generated_text="期限切れ投稿",
            scheduled_at=self.overdue_scheduled_at(),
        )

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(url, HTTP_REQUEST_TOKEN="test-token")

        self.assertEqual(response.status_code, 200)
        queue.refresh_from_db()
        self.assertEqual(queue.status, "skipped")
        self.assertIn("24時間以上", queue.error_message)
        mock_post.assert_not_called()

    @patch("twitter.views.upload_media")
    @patch("twitter.views.post_tweet")
    def test_post_with_image(self, mock_post, mock_upload):
        """画像URL付きキューが画像をアップロードして投稿される"""
        mock_upload.return_value = "media_123"
        mock_post.return_value = {"ok": True, "data": {"id": "55555", "text": "画像付きツイート"}, "status_code": None, "error_body": None}

        TweetQueue.objects.create(
            tweet_type="new_community",
            community=self.community,
            event=self.event,
            status="ready",
            generated_text="画像付きツイート",
            image_url="https://data.vrc-ta-hub.com/community/1/poster.webp",
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

        # upload_media が呼ばれたことを確認
        mock_upload.assert_called_once_with("https://data.vrc-ta-hub.com/community/1/poster.webp")
        # post_tweet に media_ids が渡されたことを確認
        mock_post.assert_called_once_with("画像付きツイート", media_ids=["media_123"])

    @patch("twitter.views.upload_media")
    @patch("twitter.views.post_tweet")
    def test_post_with_image_upload_failure(self, mock_post, mock_upload):
        """画像アップロード失敗時でもテキストだけで投稿される"""
        mock_upload.return_value = None
        mock_post.return_value = {"ok": True, "data": {"id": "66666", "text": "テキストのみ"}, "status_code": None, "error_body": None}

        TweetQueue.objects.create(
            tweet_type="new_community",
            community=self.community,
            event=self.event,
            status="ready",
            generated_text="テキストのみ",
            image_url="https://data.vrc-ta-hub.com/community/1/poster.webp",
            scheduled_at=self.due_scheduled_at(),
        )

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(
                url, HTTP_REQUEST_TOKEN="test-token",
            )

        self.assertEqual(response.status_code, 200)
        # media_ids=None で投稿される
        mock_post.assert_called_once_with("テキストのみ", media_ids=None)
