"""資料共有シグナルのテスト。"""

import datetime
from unittest.mock import MagicMock, patch

import requests
from django.test import override_settings
from django.utils import timezone

from event.models import Event, EventDetail
from twitter.models import TweetQueue

from twitter.tests._auto_tweet_test_base import AutoTweetTestBase

class SlideShareSignalTest(AutoTweetTestBase):
    """スライド/記事共有時のシグナルテスト"""

    def setUp(self):
        super().setUp()
        # community を approved にしておく
        with patch("twitter.services.tweet_generation.threading.Thread") as mock_thread_cls:
            mock_thread_cls.return_value = MagicMock()
            self.community.status = "approved"
            self.community.save()
        TweetQueue.objects.all().delete()

        # 過去の日付のイベントを作成
        self.past_event = Event.objects.create(
            community=self.community,
            date=datetime.date(2025, 1, 1),  # 過去の日付
            start_time=datetime.time(22, 0),
            duration=60,
        )
        # 承認済みの EventDetail を作成（slide_url/youtube_url なし）
        with patch("twitter.services.tweet_generation.threading.Thread") as mock_thread_cls:
            mock_thread_cls.return_value = MagicMock()
            self.detail = EventDetail.objects.create(
                event=self.past_event,
                detail_type="LT",
                status="approved",
                speaker="テスト太郎",
                theme="VRChatで学ぶPython",
                start_time=datetime.time(22, 15),
            )
        # LT承認時のキューをクリア
        TweetQueue.objects.all().delete()

    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_slide_url_first_set_creates_queue(self, mock_thread_cls):
        """slide_url が初めて設定され、発表日が過去ならキューが作成される"""
        mock_thread_cls.return_value = MagicMock()

        self.detail.slide_url = "https://example.com/slides"
        self.detail.save()

        self.assertEqual(TweetQueue.objects.count(), 1)
        queue = TweetQueue.objects.first()
        self.assertEqual(queue.tweet_type, "slide_share")
        self.assertEqual(queue.event_detail, self.detail)
        self.assertEqual(queue.event, self.past_event)
        self.assertEqual(queue.status, "generating")
        self.assertEqual(timezone.localtime(queue.scheduled_at).hour, 10)
        mock_thread_cls.assert_called_once()

    @patch("event.notifications.post_discord_webhook")
    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_slide_share_sends_community_webhook(self, mock_thread_cls, mock_post):
        """資料公開時は集会に設定したWebhookへ通知を送る"""
        mock_thread_cls.return_value = MagicMock()
        mock_post.return_value = MagicMock(status_code=200)
        self.community.notification_webhook_url = "https://discord.com/api/webhooks/123/abc"
        self.community.save(update_fields=["notification_webhook_url"])

        self.detail.slide_url = "https://example.com/slides"
        self.detail.save()

        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args[0][0], self.community.notification_webhook_url)
        payload = mock_post.call_args.args[1]
        self.assertEqual(payload["content"], "📚 **資料公開のお知らせ**")
        self.assertEqual(payload["embeds"][0]["title"], "登壇資料が公開されました")
        self.assertEqual(payload["embeds"][0]["description"], f"**{self.detail.theme}**")
        self.assertIn("event/detail", payload["embeds"][0]["fields"][2]["value"])
        self.assertIn("event/detail", payload["embeds"][0]["url"])

    @override_settings(
        AWS_S3_CUSTOM_DOMAIN="data.vrc-ta-hub.com",
        MEDIA_URL="https://data.vrc-ta-hub.com/",
    )
    @patch("event.notifications.post_discord_webhook")
    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_slide_share_webhook_uses_event_detail_thumbnail_image(
        self, mock_thread_cls, mock_post,
    ):
        """資料公開通知にはEventDetailのOGP画像を表示する."""
        mock_thread_cls.return_value = MagicMock()
        mock_post.return_value = MagicMock(status_code=200)
        self.community.notification_webhook_url = "https://discord.com/api/webhooks/123/abc"
        self.community.save(update_fields=["notification_webhook_url"])

        self.detail.thumbnail_image = "thumbnail/generated.jpg"
        self.detail.slide_url = "https://example.com/slides"
        self.detail.save()

        payload = mock_post.call_args.args[1]
        image_url = payload["embeds"][0]["image"]["url"]
        self.assertEqual(image_url, "https://data.vrc-ta-hub.com/thumbnail/generated.jpg")

    @override_settings(
        AWS_S3_CUSTOM_DOMAIN="data.vrc-ta-hub.com",
        MEDIA_URL="https://data.vrc-ta-hub.com/",
    )
    @patch("event.notifications.post_discord_webhook")
    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_slide_share_webhook_falls_back_to_community_poster_image(
        self, mock_thread_cls, mock_post,
    ):
        """EventDetail画像がない場合は集会ポスターを表示する."""
        mock_thread_cls.return_value = MagicMock()
        mock_post.return_value = MagicMock(status_code=200)
        self.community.notification_webhook_url = "https://discord.com/api/webhooks/123/abc"
        self.community.poster_image = "poster/community.webp"
        self.community.save(update_fields=["notification_webhook_url", "poster_image"])

        self.detail.slide_url = "https://example.com/slides"
        self.detail.save()

        payload = mock_post.call_args.args[1]
        image_url = payload["embeds"][0]["image"]["url"]
        self.assertEqual(image_url, "https://data.vrc-ta-hub.com/poster/community.webp")

    @patch("event.notifications.post_discord_webhook")
    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_slide_share_without_webhook_does_not_send_notification(self, mock_thread_cls, mock_post):
        """Webhook未設定なら資料公開通知は送らない"""
        mock_thread_cls.return_value = MagicMock()

        self.detail.slide_url = "https://example.com/slides"
        self.detail.save()

        mock_post.assert_not_called()

    @patch("event.notifications.post_discord_webhook")
    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_slide_share_webhook_failure_does_not_block_queue_creation(
        self, mock_thread_cls, mock_post,
    ):
        """Webhook失敗を安全にlogし、slide_shareキュー作成は継続する."""
        sensitive_url = "https://discord.com/api/webhooks/123456789/secret-token"
        mock_post.side_effect = requests.RequestException(
            f"timeout for {sensitive_url}",
        )
        mock_thread_cls.return_value = MagicMock()
        self.community.notification_webhook_url = "https://discord.com/api/webhooks/123/abc"
        self.community.save(update_fields=["notification_webhook_url"])

        with self.assertLogs(
            "event.notifications",
            level="ERROR",
        ) as log_context:
            self.detail.slide_url = "https://example.com/slides"
            self.detail.save()

        self.assertEqual(TweetQueue.objects.count(), 1)
        mock_post.assert_called_once()
        logs = "\n".join(log_context.output)
        self.assertIn("error_type=RequestException", logs)
        self.assertIn("status_code=None", logs)
        self.assertNotIn(sensitive_url, logs)
        self.assertNotIn("secret-token", logs)
        self.assertNotIn("timeout for", logs)
        self.assertNotIn("Traceback", logs)

    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_youtube_url_first_set_creates_queue(self, mock_thread_cls):
        """youtube_url が初めて設定され、発表日が過去ならキューが作成される"""
        mock_thread_cls.return_value = MagicMock()

        self.detail.youtube_url = "https://youtube.com/watch?v=test123"
        self.detail.save()

        self.assertEqual(TweetQueue.objects.count(), 1)
        queue = TweetQueue.objects.first()
        self.assertEqual(queue.tweet_type, "slide_share")

    @patch("event.notifications.post_discord_webhook")
    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_youtube_only_does_not_send_slide_webhook(self, mock_thread_cls, mock_post):
        """YouTubeのみ追加した場合はスライドWebhook通知を送らない"""
        mock_thread_cls.return_value = MagicMock()
        self.community.notification_webhook_url = "https://discord.com/api/webhooks/123/abc"
        self.community.save(update_fields=["notification_webhook_url"])

        self.detail.youtube_url = "https://youtube.com/watch?v=test123"
        self.detail.save()

        self.assertEqual(TweetQueue.objects.count(), 1)
        queue = TweetQueue.objects.first()
        self.assertEqual(queue.tweet_type, "slide_share")
        mock_post.assert_not_called()

    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_future_event_does_not_create_queue(self, mock_thread_cls):
        """発表日が未来の場合はキューが作成されない"""
        mock_thread_cls.return_value = MagicMock()

        # 未来のイベントに紐づくEventDetail
        future_event = Event.objects.create(
            community=self.community,
            date=datetime.date(2099, 12, 31),
            start_time=datetime.time(22, 0),
            duration=60,
        )
        with patch("twitter.services.tweet_generation.threading.Thread") as mt:
            mt.return_value = MagicMock()
            future_detail = EventDetail.objects.create(
                event=future_event,
                detail_type="LT",
                status="approved",
                speaker="テスト太郎",
                theme="未来のテーマ",
                start_time=datetime.time(22, 15),
            )
        TweetQueue.objects.all().delete()

        future_detail.slide_url = "https://example.com/slides"
        future_detail.save()

        self.assertEqual(TweetQueue.objects.count(), 0)

    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_duplicate_slide_share_prevention(self, mock_thread_cls):
        """同じ event_detail の slide_share キューは重複作成されない"""
        mock_thread_cls.return_value = MagicMock()

        self.detail.slide_url = "https://example.com/slides"
        self.detail.save()
        self.assertEqual(TweetQueue.objects.count(), 1)

        # youtube_url も追加 → 重複なので作成されない
        self.detail.youtube_url = "https://youtube.com/watch?v=test123"
        self.detail.save()
        self.assertEqual(TweetQueue.objects.count(), 1)

    @override_settings(AWS_S3_CUSTOM_DOMAIN='data.vrc-ta-hub.com')
    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_existing_unposted_slide_share_queue_syncs_thumbnail_image(self, mock_thread_cls):
        """既存の未投稿slide_shareキューはサムネイルURLへ再同期される"""
        mock_thread_cls.return_value = MagicMock()
        self.community.poster_image = "poster/community.webp"
        self.community.save(update_fields=["poster_image"])
        queue = TweetQueue.objects.create(
            tweet_type="slide_share",
            community=self.community,
            event=self.past_event,
            event_detail=self.detail,
            status="ready",
            image_url="https://data.vrc-ta-hub.com/cdn-cgi/image/width=960,quality=80,format=auto/poster/community.webp",
        )

        self.detail.slide_file = "slide/test.pdf"
        self.detail.thumbnail_image = "thumbnail/generated.jpg"
        self.detail.save()

        queue.refresh_from_db()
        self.assertEqual(TweetQueue.objects.count(), 1)
        self.assertIn("thumbnail/generated.jpg", queue.image_url)
        self.assertNotIn("poster/community.webp", queue.image_url)

    @override_settings(AWS_S3_CUSTOM_DOMAIN='data.vrc-ta-hub.com')
    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_posted_slide_share_queue_image_is_not_changed(self, mock_thread_cls):
        """投稿済みslide_shareキューの画像URLは履歴として保持する"""
        mock_thread_cls.return_value = MagicMock()
        poster_url = (
            "https://data.vrc-ta-hub.com/cdn-cgi/image/"
            "width=960,quality=80,format=auto/poster/community.webp"
        )
        queue = TweetQueue.objects.create(
            tweet_type="slide_share",
            community=self.community,
            event=self.past_event,
            event_detail=self.detail,
            status="posted",
            image_url=poster_url,
            tweet_id="1234567890",
        )

        self.detail.slide_file = "slide/test.pdf"
        self.detail.thumbnail_image = "thumbnail/generated.jpg"
        self.detail.save()

        queue.refresh_from_db()
        self.assertEqual(TweetQueue.objects.count(), 1)
        self.assertEqual(queue.image_url, poster_url)

    @patch("event.notifications.post_discord_webhook")
    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_slide_webhook_still_sent_when_youtube_queue_already_exists(
        self, mock_thread_cls, mock_post,
    ):
        """YouTube先行でキュー済みでも、後からスライド追加したらWebhookは送る"""
        mock_thread_cls.return_value = MagicMock()
        mock_post.return_value = MagicMock(status_code=200)
        self.community.notification_webhook_url = "https://discord.com/api/webhooks/123/abc"
        self.community.save(update_fields=["notification_webhook_url"])

        self.detail.youtube_url = "https://youtube.com/watch?v=test123"
        self.detail.save()
        self.assertEqual(TweetQueue.objects.count(), 1)
        mock_post.assert_not_called()

        self.detail.slide_url = "https://example.com/slides"
        self.detail.save()

        self.assertEqual(TweetQueue.objects.count(), 1)
        mock_post.assert_called_once()

    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_slide_url_update_does_not_create_queue(self, mock_thread_cls):
        """既に slide_url があるものを更新してもキューは作成されない"""
        mock_thread_cls.return_value = MagicMock()

        # まず slide_url を設定
        self.detail.slide_url = "https://example.com/slides"
        self.detail.save()
        TweetQueue.objects.all().delete()

        # 別のURLに更新
        self.detail.slide_url = "https://example.com/slides-v2"
        self.detail.save()

        self.assertEqual(TweetQueue.objects.count(), 0)

    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_blog_type_does_not_create_slide_share_queue(self, mock_thread_cls):
        """BLOG タイプではスライド共有キューが作成されない"""
        mock_thread_cls.return_value = MagicMock()

        with patch("twitter.services.tweet_generation.threading.Thread") as mt:
            mt.return_value = MagicMock()
            blog_detail = EventDetail.objects.create(
                event=self.past_event,
                detail_type="BLOG",
                status="approved",
                speaker="ブロガー",
                theme="振り返り",
                start_time=datetime.time(22, 0),
            )
        TweetQueue.objects.all().delete()

        blog_detail.slide_url = "https://example.com/article"
        blog_detail.save()

        self.assertEqual(TweetQueue.objects.count(), 0)

    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_pending_detail_does_not_create_slide_share_queue(self, mock_thread_cls):
        """未承認の EventDetail ではスライド共有キューが作成されない"""
        mock_thread_cls.return_value = MagicMock()

        with patch("twitter.services.tweet_generation.threading.Thread") as mt:
            mt.return_value = MagicMock()
            pending_detail = EventDetail.objects.create(
                event=self.past_event,
                detail_type="LT",
                status="pending",
                speaker="未承認太郎",
                theme="未承認テーマ",
                start_time=datetime.time(22, 30),
            )
        TweetQueue.objects.all().delete()

        pending_detail.slide_url = "https://example.com/slides"
        pending_detail.save()

        self.assertEqual(TweetQueue.objects.count(), 0)

    @patch("event.services.media_service.ensure_pdf_thumbnail", return_value=False)
    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_slide_file_first_set_creates_queue(self, mock_thread_cls, _mock_ensure_pdf_thumbnail):
        """slide_file が初めて設定され、発表日が過去ならキューが作成される"""
        mock_thread_cls.return_value = MagicMock()

        self.detail.slide_file = "slide/test.pdf"
        self.detail.save()

        self.assertEqual(TweetQueue.objects.count(), 1)
        queue = TweetQueue.objects.first()
        self.assertEqual(queue.tweet_type, "slide_share")
