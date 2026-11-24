"""予約投稿エンドポイントの基本動作テスト。"""

import datetime
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from event.models import Event, EventDetail
from tests.factories import make_event, make_event_detail
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
    def test_post_scheduled_tweets_skips_retired_daily_reminder_for_today_event(self, mock_post):
        """廃止された daily_reminder は当日イベントでも投稿せず skipped にする"""
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
        self.assertEqual(data["results"][0]["status"], "skipped")
        self.assertEqual(data["results"][0]["reason"], "daily_reminder_retired")

        queue = TweetQueue.objects.get(tweet_type="daily_reminder")
        self.assertEqual(queue.status, "skipped")
        self.assertIn("廃止", queue.error_message)
        mock_post.assert_not_called()

    @patch("twitter.views.post_tweet")
    def test_post_scheduled_tweets_posts_same_day_individual_queue(self, mock_post):
        """当日の開始前なら個別 LT キューもURLがなければそのまま投稿する"""
        mock_post.return_value = {
            "ok": True,
            "data": {"id": "lt-today-123", "text": "今日の個別LT告知"},
            "status_code": None,
            "error_body": None,
        }
        today = timezone.localdate()
        before_start = timezone.make_aware(
            datetime.datetime.combine(today, datetime.time(20, 0)),
            timezone.get_current_timezone(),
        )
        today_event = Event.objects.create(
            community=self.community,
            date=today,
            start_time=datetime.time(21, 0),
            duration=60,
        )
        queue = TweetQueue.objects.create(
            tweet_type="lt",
            community=self.community,
            event=today_event,
            status="ready",
            generated_text="11/24(火) 21:00~ テスト集会\n\nテスト太郎「テーマ」\n\nVRChatグループID: TEST.1234",
            scheduled_at=before_start - datetime.timedelta(minutes=5),
        )

        with (
            patch("twitter.services.tweet_scheduling_service.timezone.now", return_value=before_start),
            patch.dict("os.environ", self.REQUEST_TOKEN_ENV),
        ):
            url = reverse("twitter:post_scheduled_tweets")
            response = self.client.get(url, HTTP_REQUEST_TOKEN="test-token")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["created"], 0)
        self.assertEqual(data["results"][0]["status"], "posted")
        queue.refresh_from_db()
        self.assertEqual(queue.status, "posted")
        self.assertEqual(queue.tweet_id, "lt-today-123")
        mock_post.assert_called_once()

    @patch("twitter.views.post_tweet")
    def test_post_scheduled_tweets_skips_after_event_start_and_on_retracted_detail(self, mock_post):
        """開始時刻経過後や承認取消/論理削除/種別変更済みの発表は投稿前にスキップする"""
        today = timezone.localdate()
        after_start = timezone.make_aware(
            datetime.datetime.combine(today, datetime.time(21, 5)),
            timezone.get_current_timezone(),
        )
        today_event = make_event(
            self.community,
            event_date=today,
            start_time=datetime.time(21, 0),
            duration=60,
        )
        started_queue = TweetQueue.objects.create(
            tweet_type="lt",
            community=self.community,
            event=today_event,
            status="ready",
            generated_text="11/24(火) 21:00~ テスト\n\n太郎「発表」\n\nVRChatグループID: GAME.2515",
            scheduled_at=after_start - datetime.timedelta(minutes=5),
        )

        with (
            patch("twitter.services.tweet_scheduling_service.timezone.now", return_value=after_start),
            patch.dict("os.environ", self.REQUEST_TOKEN_ENV),
        ):
            response = self.client.get(reverse("twitter:post_scheduled_tweets"), HTTP_REQUEST_TOKEN="test-token")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["reason"], "event_started")
        started_queue.refresh_from_db()
        self.assertEqual(started_queue.status, "skipped")

        # EventDetailQuerySet.delete() によるシグナルなし論理削除でも配信直前にスキップする
        future_detail = make_event_detail(
            self.event,
            detail_type="LT",
            status="approved",
            speaker="花子",
            theme="取り下げ発表",
            start_time=datetime.time(22, 15),
        )
        future_queue = TweetQueue.objects.create(
            tweet_type="lt",
            community=self.community,
            event=self.event,
            event_detail=future_detail,
            status="ready",
            generated_text="5/1(金) 22:00~ テスト\n\n花子「取り下げ発表」\n\nVRChatグループID: GAME.2515",
            scheduled_at=self.due_scheduled_at(),
        )
        EventDetail.objects.filter(pk=future_detail.pk).delete()

        with patch.dict("os.environ", self.REQUEST_TOKEN_ENV):
            response = self.client.get(reverse("twitter:post_scheduled_tweets"), HTTP_REQUEST_TOKEN="test-token")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["reason"], "detail_deleted")
        future_queue.refresh_from_db()
        self.assertEqual(future_queue.status, "skipped")
        mock_post.assert_not_called()

    def test_post_scheduled_tweets_ignores_non_approved_or_non_lt_details(self):
        """approved な LT/SPECIAL がなくてもスケジューラは新規キューを作らない"""
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
    def test_post_scheduled_tweets_does_not_create_missing_daily_reminder(self, mock_post):
        """daily_reminder が未作成ならスケジューラは補完作成しない"""
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

    def test_post_tweet_queue_item_blocks_url_on_lt_and_special(self):
        """lt / special に URL が含まれている時は X API を呼ばず失敗として止める"""
        from twitter.services.tweet_scheduling_service import post_tweet_queue_item

        for tweet_type in ("lt", "special"):
            for bad_url in (
                "https://vrc-ta-hub.com/community/1/",
                "twitter.みんな",
                "пример.com",
                "example.xn--vermgensberater-ctb",
                "example.zippo",
                "//example.xperia",
            ):
                with self.subTest(tweet_type=tweet_type, bad_url=bad_url):
                    queue = TweetQueue.objects.create(
                        tweet_type=tweet_type,
                        community=self.community,
                        event=self.event,
                        status="ready",
                        image_url="https://example.com/poster.jpg",
                        generated_text=f"11/25(水) 22:00~ テスト\n\n詳細 {bad_url}",
                        scheduled_at=self.due_scheduled_at(),
                    )
                    mock_upload = MagicMock()
                    mock_post = MagicMock()
                    mock_notify = MagicMock()

                    result = post_tweet_queue_item(
                        queue,
                        upload_media_func=mock_upload,
                        post_tweet_func=mock_post,
                        notify_failure_func=mock_notify,
                    )

                    self.assertEqual(result["status"], "failed")
                    self.assertEqual(result["error"], "contains_url")
                    mock_upload.assert_not_called()
                    mock_post.assert_not_called()
                    mock_notify.assert_called_once()
                    queue.refresh_from_db()
                    self.assertEqual(queue.status, "failed")
                    self.assertIn("URLを含むためX投稿をブロック", queue.error_message)

    def test_post_tweet_queue_item_allows_url_on_slide_share_and_new_community(self):
        """slide_share / new_community は従来どおり URL 付きで投稿できる"""
        from twitter.services.tweet_scheduling_service import post_tweet_queue_item

        for tweet_type in ("slide_share", "new_community"):
            with self.subTest(tweet_type=tweet_type):
                queue = TweetQueue.objects.create(
                    tweet_type=tweet_type,
                    community=self.community,
                    event=self.event,
                    status="ready",
                    generated_text="解説記事を公開しました\n\nhttps://vrc-ta-hub.com/community/1/",
                    scheduled_at=self.due_scheduled_at(),
                )
                mock_post = MagicMock(
                    return_value={"ok": True, "data": {"id": f"id-{tweet_type}"}, "status_code": None, "error_body": None}
                )

                result = post_tweet_queue_item(
                    queue,
                    post_tweet_func=mock_post,
                    notify_failure_func=MagicMock(),
                )

                self.assertEqual(result["status"], "posted")
                mock_post.assert_called_once()

    @patch("twitter.views.post_tweet")
    def test_post_scheduled_tweets_rechecks_event_start_after_retry_generation(self, mock_post):
        """retry_items の同期生成中にイベント開始時刻を過ぎた時は ready アイテムを投稿せずスキップする"""
        today = timezone.localdate()
        before_start = timezone.make_aware(
            datetime.datetime.combine(today, datetime.time(21, 59)),
            timezone.get_current_timezone(),
        )
        after_start = timezone.make_aware(
            datetime.datetime.combine(today, datetime.time(22, 1)),
            timezone.get_current_timezone(),
        )
        today_event = make_event(
            self.community,
            event_date=today,
            start_time=datetime.time(22, 0),
            duration=60,
        )
        detail = make_event_detail(
            today_event,
            detail_type="LT",
            status="approved",
            speaker="太郎",
            theme="直前境界テスト",
            start_time=datetime.time(22, 10),
        )
        TweetQueue.objects.create(
            tweet_type="lt",
            community=self.community,
            event=today_event,
            status="generation_failed",
            generated_text="",
            scheduled_at=before_start - datetime.timedelta(minutes=5),
        )
        ready_queue = TweetQueue.objects.create(
            tweet_type="lt",
            community=self.community,
            event=today_event,
            event_detail=detail,
            status="ready",
            generated_text="11/24(火) 22:00~ テスト\n\n太郎「直前境界テスト」\n\nVRChatグループID: GAME.2515",
            scheduled_at=before_start - datetime.timedelta(minutes=5),
        )
        clock = [before_start]

        def slow_retry(_item):
            clock[0] = after_start
            return False

        with (
            patch("twitter.services.tweet_scheduling_service.timezone.now", side_effect=lambda: clock[0]),
            patch("twitter.services.tweet_scheduling_service.retry_generation", side_effect=slow_retry),
            patch.dict("os.environ", self.REQUEST_TOKEN_ENV),
        ):
            response = self.client.get(reverse("twitter:post_scheduled_tweets"), HTTP_REQUEST_TOKEN="test-token")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["status"], "skipped")
        self.assertEqual(data["results"][0]["reason"], "event_started")
        ready_queue.refresh_from_db()
        self.assertEqual(ready_queue.status, "skipped")
        mock_post.assert_not_called()

    @patch("twitter.views.post_tweet")
    def test_post_scheduled_tweets_revives_legacy_same_day_skipped_queue(self, mock_post):
        """一度きりの移行コマンドで旧「当日リマインド統合」スキップのうち開始前・重複なしだけを復帰させ、定期配信で投稿できる"""
        from django.core.management import call_command

        from twitter.models import MisskeyNote
        from twitter.services.announcement_eligibility import (
            LEGACY_SAME_DAY_INDIVIDUAL_SKIP_REASON,
            RETRACTED_PRESENTATION_SKIP_MESSAGE,
        )

        mock_post.return_value = {"ok": True, "data": {"id": "revived-1"}, "error_body": None}
        today = timezone.localdate()
        before_start = timezone.make_aware(
            datetime.datetime.combine(today, datetime.time(15, 0)),
            timezone.get_current_timezone(),
        )

        with (
            patch("twitter.signals.timezone.now", return_value=before_start),
            patch("twitter.scheduling.timezone.now", return_value=before_start),
            patch("twitter.signals.tweet_generation.start_tweet_generation"),
        ):
            upcoming_event = make_event(
                self.community,
                event_date=today,
                start_time=datetime.time(22, 0),
                duration=60,
            )
            started_event = make_event(
                self.community,
                event_date=today,
                start_time=datetime.time(14, 0),
                duration=60,
            )
            eligible_detail = make_event_detail(
                upcoming_event,
                detail_type="LT",
                status="approved",
                speaker="復帰太郎",
                theme="旧統合告知の復帰",
                start_time=datetime.time(22, 10),
            )
            started_detail = make_event_detail(
                started_event,
                detail_type="LT",
                status="approved",
                speaker="開始済み花子",
                theme="開始済み旧告知",
                start_time=datetime.time(14, 10),
            )
            dup_detail = make_event_detail(
                upcoming_event,
                detail_type="LT",
                status="approved",
                speaker="重複次郎",
                theme="既存アクティブあり",
                start_time=datetime.time(22, 30),
            )
            misskey_preserved_detail = make_event_detail(
                upcoming_event,
                detail_type="LT",
                status="approved",
                speaker="別媒体投稿済み三郎",
                theme="Misskey投稿済みSiblingスキップあり",
                start_time=datetime.time(22, 45),
            )

        TweetQueue.objects.filter(
            event_detail__in=(
                eligible_detail,
                started_detail,
                dup_detail,
                misskey_preserved_detail,
            ),
        ).delete()

        # 同一 (event_detail, tweet_type) に古い重複スキップ行があっても新しい 1 件だけ復帰させる
        dup_same_detail_legacy = TweetQueue.objects.create(
            tweet_type="lt",
            community=self.community,
            event=upcoming_event,
            event_detail=eligible_detail,
            status="skipped",
            error_message=LEGACY_SAME_DAY_INDIVIDUAL_SKIP_REASON,
            scheduled_at=before_start - datetime.timedelta(hours=3),
        )
        legacy_queue = TweetQueue.objects.create(
            tweet_type="lt",
            community=self.community,
            event=upcoming_event,
            event_detail=eligible_detail,
            status="skipped",
            error_message=LEGACY_SAME_DAY_INDIVIDUAL_SKIP_REASON,
            scheduled_at=before_start - datetime.timedelta(hours=1),
        )
        legacy_note = MisskeyNote.objects.create(
            tweet_queue=legacy_queue,
            status="skipped",
            error_message="告知対象外のため投稿をスキップ",
            attempts=2,
        )
        started_legacy = TweetQueue.objects.create(
            tweet_type="lt",
            community=self.community,
            event=started_event,
            event_detail=started_detail,
            status="skipped",
            error_message=LEGACY_SAME_DAY_INDIVIDUAL_SKIP_REASON,
            scheduled_at=before_start - datetime.timedelta(hours=2),
        )
        TweetQueue.objects.create(
            tweet_type="lt",
            community=self.community,
            event=upcoming_event,
            event_detail=dup_detail,
            status="ready",
            generated_text="11/24(火) 22:00~ テスト\n\n重複次郎「既存アクティブあり」\n\nVRChatグループID: GAME.2515",
            scheduled_at=before_start + datetime.timedelta(hours=1),
        )
        dup_legacy = TweetQueue.objects.create(
            tweet_type="lt",
            community=self.community,
            event=upcoming_event,
            event_detail=dup_detail,
            status="skipped",
            error_message=LEGACY_SAME_DAY_INDIVIDUAL_SKIP_REASON,
            scheduled_at=before_start - datetime.timedelta(minutes=30),
        )
        misskey_preserved_sibling = TweetQueue.objects.create(
            tweet_type="lt",
            community=self.community,
            event=upcoming_event,
            event_detail=misskey_preserved_detail,
            status="skipped",
            error_message=RETRACTED_PRESENTATION_SKIP_MESSAGE,
            scheduled_at=before_start - datetime.timedelta(hours=2),
        )
        MisskeyNote.objects.create(
            tweet_queue=misskey_preserved_sibling,
            status="posted",
            note_id="mk-sibling-preserved",
        )
        misskey_preserved_legacy = TweetQueue.objects.create(
            tweet_type="lt",
            community=self.community,
            event=upcoming_event,
            event_detail=misskey_preserved_detail,
            status="skipped",
            error_message=LEGACY_SAME_DAY_INDIVIDUAL_SKIP_REASON,
            scheduled_at=before_start - datetime.timedelta(hours=1),
        )

        def fake_retry(item):
            item.status = "ready"
            item.generated_text = "11/24(火) 22:00~ テスト\n\n復帰太郎「旧統合告知の復帰」\n\nVRChatグループID: GAME.2515"
            item.save(update_fields=["status", "generated_text"])
            return True

        with (
            patch("twitter.management.commands.regenerate_ready_tweets.timezone.now", return_value=before_start),
            patch("twitter.scheduling.timezone.now", return_value=before_start),
        ):
            call_command("regenerate_ready_tweets", "--revive-legacy-same-day", "--dry-run")
            legacy_queue.refresh_from_db()
            self.assertEqual(legacy_queue.status, "skipped")

            call_command("regenerate_ready_tweets", "--revive-legacy-same-day")

        legacy_queue.refresh_from_db()
        legacy_note.refresh_from_db()
        dup_same_detail_legacy.refresh_from_db()
        started_legacy.refresh_from_db()
        dup_legacy.refresh_from_db()
        misskey_preserved_legacy.refresh_from_db()
        self.assertEqual(legacy_queue.status, "generation_failed")
        self.assertEqual(legacy_queue.scheduled_at, before_start)
        self.assertEqual(legacy_note.status, "pending")
        self.assertEqual(legacy_note.error_message, "")
        self.assertEqual(legacy_note.attempts, 0)
        self.assertEqual(dup_same_detail_legacy.status, "skipped")
        self.assertEqual(started_legacy.status, "skipped")
        self.assertEqual(dup_legacy.status, "skipped")
        self.assertEqual(misskey_preserved_legacy.status, "skipped")

        with (
            patch("twitter.services.tweet_scheduling_service.timezone.now", return_value=before_start),
            patch("twitter.scheduling.timezone.now", return_value=before_start),
            patch("twitter.services.tweet_scheduling_service.retry_generation", side_effect=fake_retry),
            patch.dict("os.environ", self.REQUEST_TOKEN_ENV),
        ):
            response = self.client.get(reverse("twitter:post_scheduled_tweets"), HTTP_REQUEST_TOKEN="test-token")

        self.assertEqual(response.status_code, 200)
        legacy_queue.refresh_from_db()
        self.assertEqual(legacy_queue.status, "posted")
        mock_post.assert_called_once()
