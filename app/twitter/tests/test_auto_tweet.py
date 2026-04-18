"""X (Twitter) 自動告知機能のテスト

シグナルによるキュー追加、非同期テキスト生成、スケジュール投稿エンドポイント、
X API 投稿関数、画像アップロード、告知文生成関数をテストする。
"""

import datetime
from django.db import IntegrityError
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from community.models import Community, CommunityMember
from event.models import Event, EventDetail
from twitter.models import TweetQueue
from twitter.scheduling import default_scheduled_at, scheduled_at_for_date

CustomUser = get_user_model()


class AutoTweetTestBase(TestCase):
    """テスト共通のセットアップ"""

    def setUp(self):
        self.client = Client()
        self.owner = CustomUser.objects.create_user(
            user_name="auto_tweet_owner",
            email="auto_tweet_owner@example.com",
            password="testpassword",
        )
        # status=pending で作成 (承認前)
        self.community = Community.objects.create(
            name="Auto Tweet Community",
            start_time=datetime.time(22, 0),
            duration=60,
            weekdays=["Mon", "Thu"],
            frequency="毎週",
            organizers="Test Organizer",
            description="テスト用の技術系集会です",
            platform="All",
            status="pending",
            twitter_hashtag="TestMeetup",
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner,
            role=CommunityMember.Role.OWNER,
        )
        self.event = Event.objects.create(
            community=self.community,
            date=datetime.date(2026, 5, 1),
            start_time=datetime.time(22, 0),
            duration=60,
        )

    def due_scheduled_at(self):
        return timezone.now() - datetime.timedelta(minutes=5)

    def future_scheduled_at(self):
        return timezone.now() + datetime.timedelta(hours=1)

    def overdue_scheduled_at(self):
        return timezone.now() - datetime.timedelta(hours=25)


class CommunityApprovalSignalTest(AutoTweetTestBase):
    """Community 承認時のシグナルテスト"""

    @patch("twitter.signals.threading.Thread")
    def test_community_approval_creates_queue(self, mock_thread_cls):
        """Community が pending -> approved に変更されたらキューが generating で作成される"""
        mock_thread = MagicMock()
        mock_thread_cls.return_value = mock_thread

        self.assertEqual(TweetQueue.objects.count(), 0)

        self.community.status = "approved"
        self.community.save()

        self.assertEqual(TweetQueue.objects.count(), 1)
        queue = TweetQueue.objects.first()
        self.assertEqual(queue.tweet_type, "new_community")
        self.assertEqual(queue.community, self.community)
        self.assertEqual(queue.status, "generating")

        # スレッドが起動されたことを確認
        mock_thread_cls.assert_called_once()
        mock_thread.start.assert_called_once()

    @patch("twitter.signals.threading.Thread")
    def test_duplicate_community_queue_prevention(self, mock_thread_cls):
        """同一 community の重複キューは作成されない"""
        mock_thread_cls.return_value = MagicMock()

        self.community.status = "approved"
        self.community.save()
        self.assertEqual(TweetQueue.objects.count(), 1)

        # 再度保存しても増えない
        self.community.status = "approved"
        self.community.save()
        self.assertEqual(TweetQueue.objects.count(), 1)

    @patch("twitter.signals.threading.Thread")
    def test_rejected_community_does_not_create_queue(self, mock_thread_cls):
        """rejected への変更ではキューは作成されない"""
        self.community.status = "rejected"
        self.community.save()

        self.assertEqual(TweetQueue.objects.count(), 0)

    @patch("twitter.signals.threading.Thread")
    def test_already_approved_community_does_not_create_queue(self, mock_thread_cls):
        """既に approved だった community の再保存ではキューは作成されない"""
        mock_thread_cls.return_value = MagicMock()

        self.community.status = "approved"
        self.community.save()
        self.assertEqual(TweetQueue.objects.count(), 1)

        # description 変更で再保存
        self.community.description = "更新しました"
        self.community.save()
        self.assertEqual(TweetQueue.objects.count(), 1)

class EventDetailSignalTest(AutoTweetTestBase):
    """EventDetail 作成/承認時のシグナルテスト"""

    def setUp(self):
        super().setUp()
        # community を approved にしておく (LT テスト用)
        with patch("twitter.signals.threading.Thread") as mock_thread_cls:
            mock_thread_cls.return_value = MagicMock()
            self.community.status = "approved"
            self.community.save()
        # community 承認時のキューをクリア
        TweetQueue.objects.all().delete()

    @patch("twitter.signals.threading.Thread")
    def test_lt_approval_creates_queue(self, mock_thread_cls):
        """LT タイプの EventDetail 承認時にキューが作成される"""
        mock_thread_cls.return_value = MagicMock()

        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="VRChatで学ぶPython",
            start_time=datetime.time(22, 15),
        )

        self.assertEqual(TweetQueue.objects.count(), 2)
        queue = TweetQueue.objects.get(tweet_type="lt")
        reminder = TweetQueue.objects.get(tweet_type="daily_reminder")
        self.assertEqual(queue.tweet_type, "lt")
        self.assertEqual(queue.event_detail, detail)
        self.assertEqual(queue.event, self.event)
        self.assertEqual(queue.status, "generating")
        self.assertEqual(queue.scheduled_at, default_scheduled_at("lt", self.event))
        self.assertEqual(reminder.event, self.event)
        self.assertEqual(reminder.scheduled_at, scheduled_at_for_date(self.event.date))

    @patch("twitter.signals.threading.Thread")
    def test_special_event_creates_queue(self, mock_thread_cls):
        """SPECIAL タイプの EventDetail 承認時にキューが作成される"""
        mock_thread_cls.return_value = MagicMock()

        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="SPECIAL",
            status="approved",
            speaker="ゲスト講師",
            theme="VR空間でのコラボレーション",
            start_time=datetime.time(22, 0),
        )

        self.assertEqual(TweetQueue.objects.count(), 2)
        queue = TweetQueue.objects.get(tweet_type="special")
        reminder = TweetQueue.objects.get(tweet_type="daily_reminder")
        self.assertEqual(queue.tweet_type, "special")
        self.assertEqual(queue.event_detail, detail)
        self.assertEqual(reminder.scheduled_at, scheduled_at_for_date(self.event.date))

    @patch("twitter.signals.threading.Thread")
    def test_blog_type_does_not_create_queue(self, mock_thread_cls):
        """BLOG タイプではキューが作成されない"""
        EventDetail.objects.create(
            event=self.event,
            detail_type="BLOG",
            status="approved",
            speaker="ブロガー",
            theme="振り返りブログ",
            start_time=datetime.time(22, 0),
        )

        self.assertEqual(TweetQueue.objects.count(), 0)

    @patch("twitter.signals.threading.Thread")
    def test_pending_detail_does_not_create_queue(self, mock_thread_cls):
        """status=pending の EventDetail ではキューが作成されない"""
        EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="pending",
            speaker="テスト太郎",
            theme="VRChatで学ぶPython",
            start_time=datetime.time(22, 15),
        )

        self.assertEqual(TweetQueue.objects.count(), 0)

    @patch("twitter.signals.threading.Thread")
    def test_duplicate_event_detail_queue_prevention_on_initial_approval(self, mock_thread_cls):
        """初回承認時、同一 event_detail の重複キューは作成されない"""
        mock_thread_cls.return_value = MagicMock()

        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="pending",
            speaker="テスト太郎",
            theme="VRChatで学ぶPython",
            start_time=datetime.time(22, 15),
        )
        # 手動でキューを作成（重複状態をシミュレート）
        TweetQueue.objects.create(
            tweet_type="lt",
            community=self.community,
            event=self.event,
            event_detail=detail,
            status="ready",
        )
        self.assertEqual(TweetQueue.objects.count(), 1)

        # pending -> approved でも既にキューがあるので増えない
        detail.status = "approved"
        detail.save()
        self.assertEqual(TweetQueue.objects.count(), 2)
        self.assertTrue(TweetQueue.objects.filter(tweet_type="daily_reminder", event=self.event).exists())

    @patch("twitter.signals.threading.Thread")
    def test_pending_to_approved_creates_queue(self, mock_thread_cls):
        """EventDetail が pending -> approved に更新されたらキューが作成される"""
        mock_thread_cls.return_value = MagicMock()

        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="pending",
            speaker="テスト太郎",
            theme="VRChatで学ぶPython",
            start_time=datetime.time(22, 15),
        )
        self.assertEqual(TweetQueue.objects.count(), 0)

        # pending -> approved
        detail.status = "approved"
        detail.save()

        self.assertEqual(TweetQueue.objects.count(), 2)
        queue = TweetQueue.objects.get(tweet_type="lt")
        self.assertEqual(queue.tweet_type, "lt")
        self.assertEqual(queue.event_detail, detail)

    @patch("twitter.signals.threading.Thread")
    def test_approved_detail_no_content_change_keeps_existing_tweet(self, mock_thread_cls):
        """既に approved の EventDetail を内容変更なしで再保存してもキューは追加されない"""
        mock_thread_cls.return_value = MagicMock()

        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="VRChatで学ぶPython",
            start_time=datetime.time(22, 15),
        )
        self.assertEqual(TweetQueue.objects.filter(tweet_type="lt").count(), 1)
        self.assertEqual(TweetQueue.objects.filter(tweet_type="daily_reminder").count(), 1)

        # 内容変更なしで再保存
        detail.save()
        self.assertEqual(TweetQueue.objects.filter(tweet_type="lt").count(), 1)
        self.assertEqual(TweetQueue.objects.filter(tweet_type="daily_reminder").count(), 1)

    @patch("twitter.signals.threading.Thread")
    def test_approved_detail_content_change_regenerates_tweet(self, mock_thread_cls):
        """approved 状態で speaker/theme が変更されたらツイートを再生成する"""
        mock_thread_cls.return_value = MagicMock()

        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="VRChatで学ぶPython",
            start_time=datetime.time(22, 15),
        )
        self.assertEqual(TweetQueue.objects.filter(tweet_type="lt").count(), 1)
        old_queue_id = TweetQueue.objects.get(tweet_type="lt").pk

        # speaker を変更
        detail.speaker = "更新太郎"
        detail.save()

        # 古いキューが削除され、新しいキューが作成される
        self.assertEqual(TweetQueue.objects.filter(tweet_type="lt").count(), 1)
        self.assertEqual(TweetQueue.objects.filter(tweet_type="daily_reminder").count(), 1)
        new_queue = TweetQueue.objects.get(tweet_type="lt")
        self.assertNotEqual(new_queue.pk, old_queue_id)
        self.assertEqual(new_queue.status, "generating")

    @patch("twitter.signals.threading.Thread")
    def test_approved_detail_theme_change_regenerates_tweet(self, mock_thread_cls):
        """approved 状態で theme が変更されたらツイートを再生成する"""
        mock_thread_cls.return_value = MagicMock()

        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="VRChatで学ぶPython",
            start_time=datetime.time(22, 15),
        )
        self.assertEqual(TweetQueue.objects.filter(tweet_type="lt").count(), 1)
        self.assertEqual(TweetQueue.objects.filter(tweet_type="daily_reminder").count(), 1)

        detail.theme = "VRChatで学ぶRust"
        detail.save()

        self.assertEqual(TweetQueue.objects.filter(tweet_type="lt").count(), 1)
        self.assertEqual(TweetQueue.objects.filter(tweet_type="daily_reminder").count(), 1)
        queue = TweetQueue.objects.get(tweet_type="lt")
        self.assertEqual(queue.status, "generating")

    @patch("twitter.signals.threading.Thread")
    def test_approved_detail_posted_tweet_not_deleted_on_change(self, mock_thread_cls):
        """投稿済みツイートは削除されず、新しいキューが追加される"""
        mock_thread_cls.return_value = MagicMock()

        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="VRChatで学ぶPython",
            start_time=datetime.time(22, 15),
        )
        # 投稿済みにする
        queue = TweetQueue.objects.get(tweet_type="lt")
        queue.status = "posted"
        queue.save()

        detail.speaker = "更新太郎"
        detail.save()

        # 投稿済みLT + 新規LT + daily_reminder = 3件
        self.assertEqual(TweetQueue.objects.filter(tweet_type="lt").count(), 2)
        self.assertEqual(TweetQueue.objects.filter(tweet_type="daily_reminder").count(), 1)
        self.assertEqual(TweetQueue.objects.filter(status="posted").count(), 1)
        self.assertEqual(TweetQueue.objects.filter(status="generating").count(), 1)

    @patch("twitter.signals.threading.Thread")
    def test_approved_detail_creates_tweet_if_none_exists_on_content_change(self, mock_thread_cls):
        """approved 状態でツイート未作成 + コンテンツ変更時に新規作成する"""
        mock_thread_cls.return_value = MagicMock()

        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="VRChatで学ぶPython",
            start_time=datetime.time(22, 15),
        )
        # 初回のキューを削除（ツイート未作成状態をシミュレート）
        TweetQueue.objects.all().delete()

        # コンテンツ変更で再保存 → 新規作成
        detail.theme = "VRChatで学ぶRust"
        detail.save()
        self.assertEqual(TweetQueue.objects.filter(tweet_type="lt").count(), 1)
        self.assertEqual(TweetQueue.objects.filter(tweet_type="daily_reminder").count(), 1)

    @patch("twitter.signals.threading.Thread")
    def test_past_event_does_not_create_lt_queue(self, mock_thread_cls):
        """過去のイベントにLTが承認されてもキューは作成されない"""
        mock_thread_cls.return_value = MagicMock()

        past_event = Event.objects.create(
            community=self.community,
            date=datetime.date(2025, 1, 1),
            start_time=datetime.time(22, 0),
            duration=60,
        )
        EventDetail.objects.create(
            event=past_event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="過去のLT",
            start_time=datetime.time(22, 15),
        )
        self.assertEqual(TweetQueue.objects.filter(tweet_type="lt").count(), 0)

    @patch("twitter.signals.threading.Thread")
    def test_past_event_content_change_does_not_create_queue(self, mock_thread_cls):
        """過去のイベントのLT内容を変更してもキューは作成されない"""
        mock_thread_cls.return_value = MagicMock()

        past_event = Event.objects.create(
            community=self.community,
            date=datetime.date(2025, 1, 1),
            start_time=datetime.time(22, 0),
            duration=60,
        )
        detail = EventDetail.objects.create(
            event=past_event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="過去のLT",
            start_time=datetime.time(22, 15),
        )
        TweetQueue.objects.all().delete()

        detail.speaker = "更新太郎"
        detail.save()
        self.assertEqual(TweetQueue.objects.filter(tweet_type="lt").count(), 0)

    @patch("twitter.views._retry_generation")
    def test_today_event_creates_skipped_lt_and_daily_reminder_queue(self, mock_retry):
        """当日のイベントでは個別告知は skipped、daily_reminder が即時作成される"""
        def mark_ready(queue):
            queue.generated_text = "今日開催のリマインド"
            queue.status = "ready"
            queue.error_message = ""
            queue.save(update_fields=["generated_text", "status", "error_message"])

        mock_retry.side_effect = mark_ready

        today_event = Event.objects.create(
            community=self.community,
            date=timezone.localdate(),
            start_time=datetime.time(22, 0),
            duration=60,
        )
        detail = EventDetail.objects.create(
            event=today_event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="当日のLT",
            start_time=datetime.time(22, 15),
        )

        lt_queue = TweetQueue.objects.get(tweet_type="lt", event_detail=detail)
        reminder_queue = TweetQueue.objects.get(tweet_type="daily_reminder", event=today_event)

        self.assertEqual(lt_queue.status, "skipped")
        self.assertIn("当日リマインド", lt_queue.error_message)
        self.assertEqual(reminder_queue.status, "ready")
        self.assertEqual(reminder_queue.generated_text, "今日開催のリマインド")
        self.assertEqual(reminder_queue.scheduled_at, scheduled_at_for_date(today_event.date))

    @patch("twitter.views._retry_generation")
    def test_today_event_theme_change_regenerates_same_daily_reminder_queue(self, mock_retry):
        """当日の LT 内容変更では same-day daily_reminder を同じキューIDのまま再生成する"""
        generated = []

        def mark_ready(queue):
            text = f"今日開催のリマインド v{len(generated) + 1}"
            generated.append(text)
            queue.generated_text = text
            queue.status = "ready"
            queue.error_message = ""
            queue.save(update_fields=["generated_text", "status", "error_message"])

        mock_retry.side_effect = mark_ready

        today_event = Event.objects.create(
            community=self.community,
            date=timezone.localdate(),
            start_time=datetime.time(22, 0),
            duration=60,
        )
        detail = EventDetail.objects.create(
            event=today_event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="当日のLT",
            start_time=datetime.time(22, 15),
        )
        reminder_queue = TweetQueue.objects.get(tweet_type="daily_reminder", event=today_event)

        detail.theme = "更新後の当日LT"
        detail.save()

        reminder_queue.refresh_from_db()
        self.assertEqual(reminder_queue.status, "ready")
        self.assertEqual(reminder_queue.generated_text, "今日開催のリマインド v2")
        self.assertEqual(TweetQueue.objects.filter(tweet_type="daily_reminder", event=today_event).count(), 1)
        self.assertEqual(TweetQueue.objects.get(tweet_type="daily_reminder", event=today_event).pk, reminder_queue.pk)

    @patch("twitter.views._retry_generation")
    def test_future_event_start_time_change_regenerates_daily_reminder_queue(self, mock_retry):
        """future イベントの開始時刻変更でも daily_reminder を同じキューIDのまま再生成する"""
        generated = []

        def mark_ready(queue):
            text = f"future reminder v{len(generated) + 1}"
            generated.append(text)
            queue.generated_text = text
            queue.status = "ready"
            queue.error_message = ""
            queue.save(update_fields=["generated_text", "status", "error_message"])

        mock_retry.side_effect = mark_ready

        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="VRChatで学ぶPython",
            start_time=datetime.time(22, 15),
        )
        reminder_queue = TweetQueue.objects.get(tweet_type="daily_reminder", event=self.event)

        detail.start_time = datetime.time(22, 30)
        detail.save()

        reminder_queue.refresh_from_db()
        self.assertEqual(reminder_queue.status, "ready")
        self.assertEqual(reminder_queue.generated_text, "future reminder v2")
        self.assertEqual(TweetQueue.objects.filter(tweet_type="daily_reminder", event=self.event).count(), 1)

    @patch("twitter.views._retry_generation")
    def test_today_event_unapprove_skips_daily_reminder(self, mock_retry):
        """当日の approved 発表がなくなったら daily_reminder は skipped になる"""
        def mark_ready(queue):
            queue.generated_text = "今日開催のリマインド"
            queue.status = "ready"
            queue.error_message = ""
            queue.save(update_fields=["generated_text", "status", "error_message"])

        mock_retry.side_effect = mark_ready

        today_event = Event.objects.create(
            community=self.community,
            date=timezone.localdate(),
            start_time=datetime.time(22, 0),
            duration=60,
        )
        detail = EventDetail.objects.create(
            event=today_event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="当日のLT",
            start_time=datetime.time(22, 15),
        )

        detail.status = "pending"
        detail.save()

        reminder_queue = TweetQueue.objects.get(tweet_type="daily_reminder", event=today_event)
        self.assertEqual(reminder_queue.status, "skipped")
        self.assertIn("承認済みの当日発表がない", reminder_queue.error_message)
        self.assertEqual(reminder_queue.generated_text, "")

class GenerateTweetAsyncTest(AutoTweetTestBase):
    """_generate_tweet_async 関数のテスト"""

    def _create_queue(self, tweet_type="new_community"):
        """テスト用にキューを作成するヘルパー"""
        return TweetQueue.objects.create(
            tweet_type=tweet_type,
            community=self.community,
            event=self.event,
            status="generating",
        )

    @patch("twitter.tweet_generator.generate_new_community_tweet")
    def test_generate_async_success(self, mock_generate):
        """テキスト生成成功時に status が ready になる"""
        mock_generate.return_value = "新しい集会の告知テスト"
        queue_item = self._create_queue()

        from twitter.signals import _generate_tweet_async
        _generate_tweet_async(queue_item.pk)

        queue_item.refresh_from_db()
        self.assertEqual(queue_item.status, "ready")
        self.assertEqual(queue_item.generated_text, "新しい集会の告知テスト")
        self.assertEqual(queue_item.error_message, "")

    @patch("twitter.tweet_generator.generate_new_community_tweet")
    def test_generate_async_failure(self, mock_generate):
        """テキスト生成失敗時に status が generation_failed になる"""
        mock_generate.return_value = None
        queue_item = self._create_queue()

        from twitter.signals import _generate_tweet_async
        _generate_tweet_async(queue_item.pk)

        queue_item.refresh_from_db()
        self.assertEqual(queue_item.status, "generation_failed")
        self.assertIn("テキスト生成に失敗", queue_item.error_message)

    @patch("twitter.tweet_generator.generate_new_community_tweet")
    def test_generate_async_exception(self, mock_generate):
        """テキスト生成中に例外が発生した場合 generation_failed になる"""
        mock_generate.side_effect = RuntimeError("LLM API error")
        queue_item = self._create_queue()

        from twitter.signals import _generate_tweet_async
        _generate_tweet_async(queue_item.pk)

        queue_item.refresh_from_db()
        self.assertEqual(queue_item.status, "generation_failed")
        self.assertIn("LLM API error", queue_item.error_message)

    def test_generate_async_nonexistent_queue(self):
        """存在しないキューIDでもエラーにならない"""
        from twitter.signals import _generate_tweet_async
        # Should not raise
        _generate_tweet_async(99999)

    @override_settings(AWS_S3_CUSTOM_DOMAIN='data.vrc-ta-hub.com')
    @patch("twitter.tweet_generator.generate_new_community_tweet")
    def test_generate_async_sets_image_url(self, mock_generate):
        """ポスター画像がある場合、CF Image Resizing URL が設定される"""
        mock_generate.return_value = "告知テスト"

        # poster_image に名前だけ設定（実ファイルは不要）
        self.community.poster_image.name = "community/1/poster.webp"
        Community.objects.filter(pk=self.community.pk).update(
            poster_image="community/1/poster.webp",
        )

        queue_item = self._create_queue()

        from twitter.signals import _generate_tweet_async
        _generate_tweet_async(queue_item.pk)

        queue_item.refresh_from_db()
        self.assertEqual(queue_item.status, "ready")
        self.assertIn("/cdn-cgi/image/width=960", queue_item.image_url)
        self.assertIn("community/1/poster.webp", queue_item.image_url)


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
    def test_post_scheduled_tweets_post_failure(self, mock_post):
        """X API 投稿失敗時の処理"""
        mock_post.return_value = {"ok": False, "data": None, "status_code": 403, "error_body": "You are not permitted to perform this action."}

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

    @patch("twitter.signals.threading.Thread")
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
    @patch("twitter.signals.threading.Thread")
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


class PostScheduledTweetsExpiredEventTest(AutoTweetTestBase):
    """投稿時のイベント日チェックテスト"""

    REQUEST_TOKEN_ENV = {"REQUEST_TOKEN": "test-token"}

    def setUp(self):
        super().setUp()
        with patch("twitter.signals.threading.Thread") as mock_thread_cls:
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
            event=self.event,  # 2026-05-01（未来）
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


class GetGeneratorHelperTest(TestCase):
    """get_generator ヘルパー関数のテスト"""

    def test_new_community_returns_callable(self):
        """new_community タイプで callable が返る"""
        from twitter.tweet_generator import get_generator
        generator = get_generator("new_community")
        self.assertIsNotNone(generator)
        self.assertTrue(callable(generator))

    def test_lt_returns_callable(self):
        """lt タイプで callable が返る"""
        from twitter.tweet_generator import get_generator
        generator = get_generator("lt")
        self.assertIsNotNone(generator)
        self.assertTrue(callable(generator))

    def test_special_returns_callable(self):
        """special タイプで callable が返る"""
        from twitter.tweet_generator import get_generator
        generator = get_generator("special")
        self.assertIsNotNone(generator)
        self.assertTrue(callable(generator))

    def test_unknown_type_returns_none(self):
        """未知の tweet_type で None が返る"""
        from twitter.tweet_generator import get_generator
        generator = get_generator("unknown")
        self.assertIsNone(generator)

    def test_empty_string_returns_none(self):
        """空文字列で None が返る"""
        from twitter.tweet_generator import get_generator
        generator = get_generator("")
        self.assertIsNone(generator)


class GetPosterImageUrlHelperTest(TestCase):
    """get_poster_image_url ヘルパー関数のテスト"""

    def setUp(self):
        with patch("twitter.signals.threading.Thread"):
            self.community = Community.objects.create(
                name="Poster Test Community",
                start_time=datetime.time(22, 0),
                duration=60,
                weekdays=["Mon"],
                frequency="毎週",
                organizers="Test",
                description="テスト用",
                platform="All",
                status="approved",
            )

    def test_no_poster_returns_empty_string(self):
        """ポスター画像がない場合は空文字列を返す"""
        from twitter.tweet_generator import get_poster_image_url
        result = get_poster_image_url(self.community)
        self.assertEqual(result, "")

    @override_settings(AWS_S3_CUSTOM_DOMAIN='data.vrc-ta-hub.com')
    def test_with_custom_domain_returns_cf_resized_url(self):
        """AWS_S3_CUSTOM_DOMAIN 設定時は CF Image Resizing URL を返す"""
        Community.objects.filter(pk=self.community.pk).update(
            poster_image="community/1/poster.webp",
        )
        self.community.refresh_from_db()

        from twitter.tweet_generator import get_poster_image_url
        result = get_poster_image_url(self.community)
        self.assertEqual(
            result,
            "https://data.vrc-ta-hub.com/cdn-cgi/image/width=960,quality=80,format=auto/community/1/poster.webp",
        )

    @override_settings(AWS_S3_CUSTOM_DOMAIN='')
    def test_without_custom_domain_falls_back_to_url(self):
        """AWS_S3_CUSTOM_DOMAIN が未設定の場合は poster.url にフォールバック"""
        Community.objects.filter(pk=self.community.pk).update(
            poster_image="community/1/poster.webp",
        )
        self.community.refresh_from_db()

        from twitter.tweet_generator import get_poster_image_url
        result = get_poster_image_url(self.community)
        # FileField に url 属性があるので何かしらの値が返る
        self.assertNotEqual(result, "")


class PostTweetFunctionTest(TestCase):
    """X API 投稿関数の単体テスト（OAuth 1.0a）"""

    OAUTH1_ENV = {
        "X_API_KEY": "test-api-key",
        "X_API_SECRET": "test-api-secret",
        "X_ACCESS_TOKEN": "test-access-token",
        "X_ACCESS_TOKEN_SECRET": "test-access-token-secret",
        "X_API_ALLOW_TEST_CALLS": "1",
    }

    @patch("twitter.x_api.requests.post")
    def test_post_tweet_success(self, mock_post):
        """正常にツイートが投稿される"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {"id": "12345", "text": "テストツイート"},
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import post_tweet
            result = post_tweet("テストツイート")

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["id"], "12345")

        # OAuth1 認証が使われていることを確認
        call_kwargs = mock_post.call_args
        self.assertIsNotNone(call_kwargs.kwargs.get("auth"))

    @patch("twitter.x_api.requests.post")
    def test_post_tweet_with_media_ids(self, mock_post):
        """media_ids 付きでツイートが投稿される"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {"id": "12345", "text": "画像付き"},
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import post_tweet
            result = post_tweet("画像付き", media_ids=["media_111"])

        self.assertTrue(result["ok"])
        # payload に media フィールドが含まれている
        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json")
        self.assertEqual(payload["media"], {"media_ids": ["media_111"]})

    def test_post_tweet_no_credentials(self):
        """環境変数が未設定の場合は ok=False を返す"""
        with patch.dict("os.environ", {
            "X_API_KEY": "",
            "X_API_SECRET": "",
            "X_ACCESS_TOKEN": "",
            "X_ACCESS_TOKEN_SECRET": "",
        }):
            from twitter.x_api import post_tweet
            result = post_tweet("テスト")
        self.assertFalse(result["ok"])
        self.assertIsNone(result["data"])

    @patch("twitter.x_api.requests.post")
    def test_post_tweet_api_error(self, mock_post):
        """API エラー時は ok=False を返す"""
        import requests
        mock_post.side_effect = requests.RequestException("API Error")

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import post_tweet
            result = post_tweet("テスト")
        self.assertFalse(result["ok"])

    @patch("twitter.x_api.requests.post")
    def test_post_tweet_api_error_with_response(self, mock_post):
        """API エラー時にレスポンスがある場合は status_code/error_body を返す"""
        import requests
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = '{"detail": "You are not permitted to perform this action."}'
        error = requests.RequestException("Forbidden")
        error.response = mock_response
        mock_post.side_effect = error

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import post_tweet
            with self.assertLogs("twitter.x_api", level="ERROR") as log_ctx:
                result = post_tweet("テスト")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status_code"], 403)
        self.assertIn("not permitted", result["error_body"])
        combined = "\n".join(log_ctx.output)
        self.assertIn("403", combined)
        self.assertIn("not permitted", combined)

    def test_post_tweet_missing_partial_credentials(self):
        """一部の認証情報だけ設定されている場合は ok=False を返す"""
        with patch.dict("os.environ", {
            "X_API_KEY": "key",
            "X_API_SECRET": "secret",
            "X_ACCESS_TOKEN": "",
            "X_ACCESS_TOKEN_SECRET": "",
        }):
            from twitter.x_api import post_tweet
            result = post_tweet("テスト")
        self.assertFalse(result["ok"])


class UploadMediaFunctionTest(TestCase):
    """upload_media 関数のテスト"""

    OAUTH1_ENV = {
        "X_API_KEY": "test-api-key",
        "X_API_SECRET": "test-api-secret",
        "X_ACCESS_TOKEN": "test-access-token",
        "X_ACCESS_TOKEN_SECRET": "test-access-token-secret",
        "X_API_ALLOW_TEST_CALLS": "1",
    }
    ALLOWED_IMAGE_URL = "https://data.vrc-ta-hub.com/community/1/poster.webp"

    def _make_stream_response(self, data=b"fake-image-data", content_type="image/webp"):
        """stream=True のレスポンスモックを生成するヘルパー"""
        mock_response = MagicMock()
        mock_response.headers = {"Content-Type": content_type}
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_content = MagicMock(return_value=[data])
        return mock_response

    @patch("twitter.x_api.requests.post")
    @patch("twitter.x_api.requests.get")
    def test_upload_media_success(self, mock_get, mock_post):
        """正常に画像がアップロードされる"""
        mock_get.return_value = self._make_stream_response()

        # メディアアップロードのモック
        mock_upload_response = MagicMock()
        mock_upload_response.json.return_value = {"media_id_string": "media_12345"}
        mock_upload_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_upload_response

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media
            result = upload_media(self.ALLOWED_IMAGE_URL)

        self.assertEqual(result, "media_12345")

    @patch("twitter.x_api.requests.get")
    def test_upload_media_download_failure(self, mock_get):
        """画像ダウンロード失敗時は None を返す"""
        import requests
        mock_get.side_effect = requests.RequestException("Download failed")

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media
            result = upload_media(self.ALLOWED_IMAGE_URL)

        self.assertIsNone(result)

    def test_upload_media_no_credentials(self):
        """認証情報がない場合は None を返す"""
        with patch.dict("os.environ", {
            "X_API_KEY": "",
            "X_API_SECRET": "",
            "X_ACCESS_TOKEN": "",
            "X_ACCESS_TOKEN_SECRET": "",
        }):
            from twitter.x_api import upload_media
            result = upload_media(self.ALLOWED_IMAGE_URL)
        self.assertIsNone(result)

    @patch("twitter.x_api.requests.post")
    @patch("twitter.x_api.requests.get")
    def test_upload_media_upload_failure(self, mock_get, mock_post):
        """X API へのアップロード失敗時は None を返す"""
        import requests

        mock_get.return_value = self._make_stream_response(content_type="image/png")
        mock_post.side_effect = requests.RequestException("Upload failed")

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media
            result = upload_media(self.ALLOWED_IMAGE_URL)

        self.assertIsNone(result)

    @patch("twitter.x_api.requests.post")
    @patch("twitter.x_api.requests.get")
    def test_upload_media_upload_failure_with_response(self, mock_get, mock_post):
        """X API アップロード失敗時にレスポンスがある場合はステータスとボディをログ出力する"""
        import requests

        mock_get.return_value = self._make_stream_response(content_type="image/png")
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = '{"errors": [{"message": "media type not allowed"}]}'
        error = requests.RequestException("Forbidden")
        error.response = mock_response
        mock_post.side_effect = error

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media
            with self.assertLogs("twitter.x_api", level="ERROR") as log_ctx:
                result = upload_media(self.ALLOWED_IMAGE_URL)

        self.assertIsNone(result)
        combined = "\n".join(log_ctx.output)
        self.assertIn("403", combined)
        self.assertIn("media type not allowed", combined)

    # --- SSRF防止テスト ---
    def test_upload_media_blocks_untrusted_domain(self):
        """許可リストにないドメインからの画像ダウンロードを拒否する"""
        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media
            result = upload_media("https://evil.example.com/malicious.png")
        self.assertIsNone(result)

    def test_upload_media_blocks_localhost(self):
        """localhost からの画像ダウンロードを拒否する"""
        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media
            result = upload_media("http://localhost:8080/internal-api")
        self.assertIsNone(result)

    def test_upload_media_blocks_internal_ip(self):
        """内部IPアドレスからの画像ダウンロードを拒否する"""
        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media
            result = upload_media("http://169.254.169.254/latest/meta-data/")
        self.assertIsNone(result)

    @patch("twitter.x_api.requests.post")
    @patch("twitter.x_api.requests.get")
    def test_upload_media_allows_trusted_domain(self, mock_get, mock_post):
        """許可ドメインからのダウンロードは成功する"""
        mock_get.return_value = self._make_stream_response()

        mock_upload_response = MagicMock()
        mock_upload_response.json.return_value = {"media_id_string": "media_ok"}
        mock_upload_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_upload_response

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media
            result = upload_media("https://data.vrc-ta-hub.com/poster.webp")

        self.assertEqual(result, "media_ok")

    @patch("twitter.x_api.requests.post")
    @patch("twitter.x_api.requests.get")
    def test_upload_media_allows_cf_transform_url(self, mock_get, mock_post):
        """CF Image Resizing URL も許可ドメインとして通過する"""
        mock_get.return_value = self._make_stream_response()
        mock_upload_response = MagicMock()
        mock_upload_response.json.return_value = {"media_id_string": "media_cf"}
        mock_upload_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_upload_response

        cf_url = (
            "https://data.vrc-ta-hub.com/cdn-cgi/image/"
            "width=960,quality=80,format=auto/community/1/poster.webp"
        )
        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media
            result = upload_media(cf_url)

        self.assertEqual(result, "media_cf")

    # --- サイズ制限テスト ---
    @patch("twitter.x_api.requests.get")
    def test_upload_media_rejects_oversized_image(self, mock_get):
        """5MB超の画像は拒否される"""
        # 6MB のチャンクを返すモック
        oversized_data = b"x" * (6 * 1024 * 1024)
        mock_get.return_value = self._make_stream_response(data=oversized_data)

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media
            result = upload_media(self.ALLOWED_IMAGE_URL)

        self.assertIsNone(result)

    @patch("twitter.x_api.requests.get")
    def test_upload_media_rejects_oversized_chunked_image(self, mock_get):
        """複数チャンクで合計5MB超の場合も拒否される"""
        chunk_size = 1024 * 1024  # 1MB per chunk
        chunks = [b"x" * chunk_size for _ in range(6)]  # 6MB total

        mock_response = MagicMock()
        mock_response.headers = {"Content-Type": "image/png"}
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_content = MagicMock(return_value=iter(chunks))
        mock_get.return_value = mock_response

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media
            result = upload_media(self.ALLOWED_IMAGE_URL)

        self.assertIsNone(result)

    @patch("twitter.x_api.requests.post")
    @patch("twitter.x_api.requests.get")
    def test_upload_media_accepts_exactly_5mb(self, mock_get, mock_post):
        """ちょうど5MBの画像は受け入れられる"""
        exactly_5mb = b"x" * (5 * 1024 * 1024)
        mock_get.return_value = self._make_stream_response(data=exactly_5mb)

        mock_upload_response = MagicMock()
        mock_upload_response.json.return_value = {"media_id_string": "media_5mb"}
        mock_upload_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_upload_response

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media
            result = upload_media(self.ALLOWED_IMAGE_URL)

        self.assertEqual(result, "media_5mb")


class TweetGeneratorTest(TestCase):
    """告知文生成関数のテスト"""

    def setUp(self):
        with patch("twitter.signals.threading.Thread"):
            self.community = Community.objects.create(
                name="Generator Test Community",
                start_time=datetime.time(22, 0),
                duration=60,
                weekdays=["Mon"],
                frequency="毎週",
                organizers="Test",
                description="テスト用集会",
                platform="All",
                status="approved",
                twitter_hashtag="GenTest",
            )
        self.event = Event.objects.create(
            community=self.community,
            date=datetime.date(2026, 5, 1),
            start_time=datetime.time(22, 0),
            duration=60,
        )

    @patch("twitter.signals.threading.Thread")
    @patch("twitter.tweet_generator._call_llm")
    def test_generate_new_community_tweet(self, mock_llm, _mock_thread):
        """新規集会の告知文が生成��れる"""
        mock_llm.return_value = "新しい集会がはじまります！"

        from twitter.tweet_generator import generate_new_community_tweet
        result = generate_new_community_tweet(self.community, self.event)

        self.assertEqual(result, "新しい集会がはじまります！")
        mock_llm.assert_called_once()

        # プロンプトに集会名が含まれていることを確認
        call_args = mock_llm.call_args
        system_prompt, user_prompt = call_args[0]
        self.assertIn("ポスト", system_prompt)
        self.assertNotIn("ツイート", system_prompt)
        self.assertIn("Generator Test Community", call_args[0][1])
        self.assertIn("告知ポスト", user_prompt)
        self.assertNotIn("告知ツイート", user_prompt)

    @patch("twitter.signals.threading.Thread")
    @patch("twitter.tweet_generator._call_llm")
    def test_generate_lt_tweet(self, mock_llm, _mock_thread):
        """LT 告知文が生成される"""
        mock_llm.return_value = "LT告知テスト"

        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="Pythonのテスト技法",
            start_time=datetime.time(22, 15),
        )

        from twitter.tweet_generator import generate_lt_tweet
        result = generate_lt_tweet(detail)

        self.assertEqual(result, "LT告知テスト")
        call_args = mock_llm.call_args
        system_prompt, user_prompt = call_args[0]
        self.assertIn("告知ポスト", system_prompt)
        self.assertNotIn("告知ツイート", system_prompt)
        self.assertIn("テスト太郎", user_prompt)
        self.assertIn("Pythonのテスト技法", user_prompt)
        self.assertIn("告知ポスト", user_prompt)
        self.assertNotIn("告知ツイート", user_prompt)

    @patch("twitter.signals.threading.Thread")
    @patch("twitter.tweet_generator._call_llm")
    def test_generate_special_event_tweet(self, mock_llm, _mock_thread):
        """特別回告知文が生成される"""
        mock_llm.return_value = "特別回告知テスト"

        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="SPECIAL",
            status="approved",
            speaker="ゲスト講師",
            theme="VRの未来",
            start_time=datetime.time(22, 0),
        )

        from twitter.tweet_generator import generate_special_event_tweet
        result = generate_special_event_tweet(detail)

        self.assertEqual(result, "特別回告知テスト")
        call_args = mock_llm.call_args
        system_prompt, user_prompt = call_args[0]
        self.assertIn("告知ポスト", system_prompt)
        self.assertNotIn("告知ツイート", system_prompt)
        self.assertIn("ゲスト講師", user_prompt)
        self.assertIn("告知ポスト", user_prompt)
        self.assertNotIn("告知ツイート", user_prompt)

    @patch("twitter.signals.threading.Thread")
    @patch("twitter.tweet_generator._call_llm")
    def test_generate_daily_reminder_tweet(self, mock_llm, _mock_thread):
        """当日リマインド生成時に開催情報と発表情報をプロンプトへ含める"""
        mock_llm.return_value = "今日開催のリマインド"

        today_event = Event.objects.create(
            community=self.community,
            date=timezone.localdate(),
            start_time=datetime.time(20, 0),
            duration=60,
        )
        EventDetail.objects.create(
            event=today_event,
            detail_type="LT",
            status="approved",
            speaker="リマインド太郎",
            theme="今日の見どころ",
            start_time=datetime.time(20, 15),
        )

        from twitter.tweet_generator import generate_daily_reminder_tweet
        result = generate_daily_reminder_tweet(today_event)

        self.assertEqual(result, "今日開催のリマインド")
        system_prompt, user_prompt = mock_llm.call_args[0]
        self.assertIn("リマインダーポスト", system_prompt)
        self.assertNotIn("リマインダーツイート", system_prompt)
        self.assertIn("今日", user_prompt)
        self.assertIn("登録発表数: 1件", user_prompt)
        self.assertIn("リマインド太郎", user_prompt)
        self.assertIn("今日の見どころ", user_prompt)
        self.assertIn("リマインダーポスト", user_prompt)
        self.assertNotIn("リマインダーツイート", user_prompt)

    @patch("twitter.signals.threading.Thread")
    def test_generate_daily_reminder_tweet_returns_none_without_approved_details(self, _mock_thread):
        """approved な LT/SPECIAL がない場合は daily reminder を生成しない"""
        today_event = Event.objects.create(
            community=self.community,
            date=timezone.localdate(),
            start_time=datetime.time(20, 0),
            duration=60,
        )
        EventDetail.objects.create(
            event=today_event,
            detail_type="BLOG",
            status="approved",
            speaker="ブロガー",
            theme="記事紹介",
            start_time=datetime.time(20, 15),
        )

        from twitter.tweet_generator import generate_daily_reminder_tweet
        result = generate_daily_reminder_tweet(today_event)

        self.assertIsNone(result)

    @patch("twitter.signals.threading.Thread")
    @patch("twitter.tweet_generator._call_llm")
    def test_generate_slide_share_tweet_with_slide(self, mock_llm, _mock_thread):
        """スライド共有ツイートが生成される（slide_url のみ）"""
        mock_llm.return_value = "スライド公開しました！"

        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="Pythonのテスト技法",
            start_time=datetime.time(22, 15),
            slide_url="https://example.com/slides",
        )

        from twitter.tweet_generator import generate_slide_share_tweet
        result = generate_slide_share_tweet(detail)

        self.assertEqual(result, "スライド公開しました！")
        call_args = mock_llm.call_args
        system_prompt, user_prompt = call_args[0]
        self.assertIn("ポスト", system_prompt)
        self.assertNotIn("ツイート", system_prompt)
        self.assertIn("ポスト", user_prompt)
        self.assertNotIn("ツイート", user_prompt)
        self.assertIn("テスト太郎", user_prompt)
        self.assertIn("Pythonのテスト技法", user_prompt)
        # URLはプロンプトに含めない（プロンプトインジェクション防止）
        self.assertNotIn("https://example.com/slides", user_prompt)
        self.assertIn("スライド", user_prompt)
        self.assertNotIn("動画", user_prompt)

    @patch("twitter.signals.threading.Thread")
    @patch("twitter.tweet_generator._call_llm")
    def test_generate_slide_share_tweet_with_youtube(self, mock_llm, _mock_thread):
        """スライド共有ツイートが生成される（youtube_url のみ）"""
        mock_llm.return_value = "動画公開しました！"

        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="VR技術入門",
            start_time=datetime.time(22, 15),
            youtube_url="https://youtube.com/watch?v=test123",
        )

        from twitter.tweet_generator import generate_slide_share_tweet
        result = generate_slide_share_tweet(detail)

        self.assertEqual(result, "動画公開しました！")
        call_args = mock_llm.call_args
        system_prompt, user_prompt = call_args[0]
        self.assertIn("ポスト", system_prompt)
        self.assertNotIn("ツイート", system_prompt)
        self.assertIn("ポスト", user_prompt)
        self.assertNotIn("ツイート", user_prompt)
        self.assertNotIn("https://youtube.com", user_prompt)
        self.assertIn("動画", user_prompt)
        self.assertNotIn("スライド", user_prompt)

    @patch("twitter.signals.threading.Thread")
    @patch("twitter.tweet_generator._call_llm")
    def test_generate_slide_share_tweet_with_both(self, mock_llm, _mock_thread):
        """スライド共有ツイートが生成される（slide_url + youtube_url 両方）"""
        mock_llm.return_value = "スライドと動画公開！"

        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="VR技術入門",
            start_time=datetime.time(22, 15),
            slide_url="https://example.com/slides",
            youtube_url="https://youtube.com/watch?v=test123",
        )

        from twitter.tweet_generator import generate_slide_share_tweet
        result = generate_slide_share_tweet(detail)

        self.assertEqual(result, "スライドと動画公開！")
        call_args = mock_llm.call_args
        system_prompt, user_prompt = call_args[0]
        self.assertIn("ポスト", system_prompt)
        self.assertNotIn("ツイート", system_prompt)
        self.assertIn("ポスト", user_prompt)
        self.assertNotIn("ツイート", user_prompt)
        self.assertIn("スライド・動画", user_prompt)

    @patch("twitter.utils._call_llm")
    def test_generate_tweet_uses_post_terminology_in_prompt(self, mock_llm):
        """テンプレートベース生成のプロンプトがX/ポスト表記に統一されている"""
        mock_llm.return_value = "テンプレートベースのポスト"

        from twitter.utils import generate_tweet
        result = generate_tweet("過去の投稿サンプル", {
            "event_name": "Generator Test Community",
            "date": "2026年4月13日(月)",
            "time": "22:00",
            "details": "22:00 - テストテーマ (テスト太郎)",
        })

        self.assertEqual(result, "テンプレートベースのポスト")
        system_prompt, user_prompt = mock_llm.call_args[0]
        self.assertIn("ポスト", system_prompt)
        self.assertNotIn("ツイート", system_prompt)
        self.assertIn("過去のポスト", user_prompt)
        self.assertIn("告知ポスト", user_prompt)
        self.assertNotIn("告知ツイート", user_prompt)

    @patch("twitter.tweet_generator._call_llm")
    def test_generate_tweet_llm_failure(self, mock_llm):
        """LLM 呼び出し失敗時は None を返す"""
        mock_llm.return_value = None

        from twitter.tweet_generator import generate_new_community_tweet
        result = generate_new_community_tweet(self.community)

        self.assertIsNone(result)

    @patch("twitter.signals.threading.Thread")
    @patch("twitter.tweet_generator._call_llm")
    def test_sanitize_strips_newlines_in_prompt(self, mock_llm, _mock_thread):
        """ユーザー入力の改行・制御文字がサニタイズされてプロンプトに渡される"""
        mock_llm.return_value = "サニタイズテスト"

        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト\n太郎\r\n",
            theme="改行\n入り\tテーマ",
            start_time=datetime.time(22, 15),
        )

        from twitter.tweet_generator import generate_lt_tweet
        generate_lt_tweet(detail)

        call_args = mock_llm.call_args
        user_prompt = call_args[0][1]
        # サニタイズ後は改行が空白に変換されている
        self.assertIn("テスト 太郎", user_prompt)
        self.assertIn("改行 入り テーマ", user_prompt)


class TweetQueueConstraintTest(TestCase):
    """TweetQueue の一意制約テスト"""

    def test_daily_reminder_unique_per_event(self):
        """daily_reminder は同一イベントに1件しか作れない"""
        community = Community.objects.create(
            name="Constraint Community",
            start_time=datetime.time(21, 0),
            duration=60,
            weekdays=["Mon"],
            frequency="毎週",
            organizers="Test",
            description="制約テスト用",
            platform="All",
            status="approved",
        )
        event = Event.objects.create(
            community=community,
            date=timezone.localdate(),
            start_time=datetime.time(21, 0),
            duration=60,
        )

        TweetQueue.objects.create(
            tweet_type="daily_reminder",
            community=community,
            event=event,
        )

        with self.assertRaises(IntegrityError):
            TweetQueue.objects.create(
                tweet_type="daily_reminder",
                community=community,
                event=event,
            )


class SanitizeForPromptTest(TestCase):
    """_sanitize_for_prompt 関数の単体テスト"""

    def test_empty_string(self):
        from twitter.tweet_generator import _sanitize_for_prompt
        self.assertEqual(_sanitize_for_prompt(""), "")

    def test_none_input(self):
        from twitter.tweet_generator import _sanitize_for_prompt
        self.assertEqual(_sanitize_for_prompt(None), "")

    def test_newlines_removed(self):
        from twitter.tweet_generator import _sanitize_for_prompt
        self.assertEqual(_sanitize_for_prompt("hello\nworld\r\n"), "hello world")

    def test_tabs_removed(self):
        from twitter.tweet_generator import _sanitize_for_prompt
        self.assertEqual(_sanitize_for_prompt("hello\tworld"), "hello world")

    def test_max_length_truncation(self):
        from twitter.tweet_generator import _sanitize_for_prompt
        long_text = "a" * 300
        result = _sanitize_for_prompt(long_text, max_length=200)
        self.assertEqual(len(result), 200)

    def test_custom_max_length(self):
        from twitter.tweet_generator import _sanitize_for_prompt
        result = _sanitize_for_prompt("abcdef", max_length=3)
        self.assertEqual(result, "abc")

    def test_multiple_spaces_collapsed(self):
        from twitter.tweet_generator import _sanitize_for_prompt
        self.assertEqual(_sanitize_for_prompt("hello   world"), "hello world")


class PostTweetValidationTest(TestCase):
    """post_tweet 関数の入力バリデーションテスト"""

    def test_empty_text_returns_failure(self):
        """空文字列で ok=False を返す"""
        from twitter.x_api import post_tweet
        result = post_tweet("")
        self.assertFalse(result["ok"])
        self.assertIsNone(result["data"])

    def test_none_text_returns_failure(self):
        """None で ok=False を返す"""
        from twitter.x_api import post_tweet
        result = post_tweet(None)
        self.assertFalse(result["ok"])

    def test_exceeds_280_chars_returns_failure(self):
        """280文字超で ok=False を返す"""
        from twitter.x_api import post_tweet
        long_text = "a" * 281
        result = post_tweet(long_text)
        self.assertFalse(result["ok"])

    def test_exactly_280_chars_does_not_reject(self):
        """280文字ちょうどはバリデーションを通過する（認証情報なしで ok=False になる）"""
        from twitter.x_api import post_tweet
        with patch.dict("os.environ", {
            "X_API_KEY": "",
            "X_API_SECRET": "",
            "X_ACCESS_TOKEN": "",
            "X_ACCESS_TOKEN_SECRET": "",
        }):
            result = post_tweet("a" * 280)
        # 認証情報がないので ok=False だが、文字数バリデーションは通過している
        self.assertFalse(result["ok"])
        # 文字数超過のエラーメッセージではないことを確認
        self.assertNotIn("too long", result["error_body"] or "")


class SlideShareSignalTest(AutoTweetTestBase):
    """スライド/記事共有時のシグナルテスト"""

    def setUp(self):
        super().setUp()
        # community を approved にしておく
        with patch("twitter.signals.threading.Thread") as mock_thread_cls:
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
        with patch("twitter.signals.threading.Thread") as mock_thread_cls:
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

    @patch("twitter.signals.threading.Thread")
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
        mock_thread_cls.assert_called_once()

    @patch("event.notifications.requests.post")
    @patch("twitter.signals.threading.Thread")
    def test_slide_share_sends_community_webhook(self, mock_thread_cls, mock_post):
        """資料公開時は集会に設定したWebhookへ通知を送る"""
        mock_thread_cls.return_value = MagicMock()
        mock_post.return_value = MagicMock(ok=True)
        self.community.notification_webhook_url = "https://discord.com/api/webhooks/123/abc"
        self.community.save(update_fields=["notification_webhook_url"])

        self.detail.slide_url = "https://example.com/slides"
        self.detail.save()

        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args[0][0], self.community.notification_webhook_url)
        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["content"], "📚 **資料公開のお知らせ**")
        self.assertEqual(payload["embeds"][0]["title"], "登壇資料が公開されました")
        self.assertEqual(payload["embeds"][0]["description"], f"**{self.detail.theme}**")
        self.assertIn("event/detail", payload["embeds"][0]["fields"][2]["value"])

    @patch("event.notifications.requests.post")
    @patch("twitter.signals.threading.Thread")
    def test_slide_share_without_webhook_does_not_send_notification(self, mock_thread_cls, mock_post):
        """Webhook未設定なら資料公開通知は送らない"""
        mock_thread_cls.return_value = MagicMock()

        self.detail.slide_url = "https://example.com/slides"
        self.detail.save()

        mock_post.assert_not_called()

    @patch("event.notifications.requests.post", side_effect=Exception("timeout"))
    @patch("twitter.signals.threading.Thread")
    def test_slide_share_webhook_failure_does_not_block_queue_creation(
        self, mock_thread_cls, mock_post,
    ):
        """Webhook送信失敗でもslide_shareキュー作成は継続する"""
        mock_thread_cls.return_value = MagicMock()
        self.community.notification_webhook_url = "https://discord.com/api/webhooks/123/abc"
        self.community.save(update_fields=["notification_webhook_url"])

        self.detail.slide_url = "https://example.com/slides"
        self.detail.save()

        self.assertEqual(TweetQueue.objects.count(), 1)
        mock_post.assert_called_once()

    @patch("twitter.signals.threading.Thread")
    def test_youtube_url_first_set_creates_queue(self, mock_thread_cls):
        """youtube_url が初めて設定され、発表日が過去ならキューが作成される"""
        mock_thread_cls.return_value = MagicMock()

        self.detail.youtube_url = "https://youtube.com/watch?v=test123"
        self.detail.save()

        self.assertEqual(TweetQueue.objects.count(), 1)
        queue = TweetQueue.objects.first()
        self.assertEqual(queue.tweet_type, "slide_share")

    @patch("event.notifications.requests.post")
    @patch("twitter.signals.threading.Thread")
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

    @patch("twitter.signals.threading.Thread")
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
        with patch("twitter.signals.threading.Thread") as mt:
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

    @patch("twitter.signals.threading.Thread")
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

    @patch("event.notifications.requests.post")
    @patch("twitter.signals.threading.Thread")
    def test_slide_webhook_still_sent_when_youtube_queue_already_exists(
        self, mock_thread_cls, mock_post,
    ):
        """YouTube先行でキュー済みでも、後からスライド追加したらWebhookは送る"""
        mock_thread_cls.return_value = MagicMock()
        mock_post.return_value = MagicMock(ok=True)
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

    @patch("twitter.signals.threading.Thread")
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

    @patch("twitter.signals.threading.Thread")
    def test_blog_type_does_not_create_slide_share_queue(self, mock_thread_cls):
        """BLOG タイプではスライド共有キューが作成されない"""
        mock_thread_cls.return_value = MagicMock()

        with patch("twitter.signals.threading.Thread") as mt:
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

    @patch("twitter.signals.threading.Thread")
    def test_pending_detail_does_not_create_slide_share_queue(self, mock_thread_cls):
        """未承認の EventDetail ではスライド共有キューが作成されない"""
        mock_thread_cls.return_value = MagicMock()

        with patch("twitter.signals.threading.Thread") as mt:
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

    @patch("twitter.signals.threading.Thread")
    def test_slide_file_first_set_creates_queue(self, mock_thread_cls):
        """slide_file が初めて設定され、発表日が過去ならキューが作成される"""
        mock_thread_cls.return_value = MagicMock()

        self.detail.slide_file = "slide/test.pdf"
        self.detail.save()

        self.assertEqual(TweetQueue.objects.count(), 1)
        queue = TweetQueue.objects.first()
        self.assertEqual(queue.tweet_type, "slide_share")

class SignalErrorHandlingTest(AutoTweetTestBase):
    """シグナルハンドラの例外がメインの保存処理を妨げないことをテスト。

    参照: PR #TBD - tweet_queue テーブル未作成時に EventDetail 保存が
    500 エラーになるインシデントの再発防止テスト。
    """

    def setUp(self):
        super().setUp()
        # community を approved にしておく
        with patch("twitter.signals.threading.Thread") as mock_thread_cls:
            mock_thread_cls.return_value = MagicMock()
            self.community.status = "approved"
            self.community.save()
        TweetQueue.objects.all().delete()

    @patch("twitter.signals._queue_new_community_tweet", side_effect=Exception("DB error"))
    def test_community_save_succeeds_on_signal_error(self, mock_queue):
        """シグナルが例外を投げても Community の保存は成功する"""
        new_community = Community.objects.create(
            name="Signal Error Test",
            start_time=datetime.time(21, 0),
            duration=60,
            weekdays=["Tue"],
            frequency="毎週",
            organizers="Test",
            description="テスト",
            platform="All",
            status="approved",
        )
        # 保存が成功していることを確認
        self.assertTrue(Community.objects.filter(pk=new_community.pk).exists())

    @patch("twitter.signals._queue_event_detail_tweet", side_effect=Exception("DB error"))
    def test_event_detail_save_succeeds_on_signal_error(self, mock_queue):
        """シグナルが例外を投げても EventDetail の保存は成功する"""
        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="テスト発表",
            start_time=datetime.time(22, 15),
        )
        # 保存が成功していることを確認
        self.assertTrue(EventDetail.objects.filter(pk=detail.pk).exists())

    @patch("twitter.signals._queue_slide_share_tweet", side_effect=Exception("DB error"))
    def test_event_detail_update_succeeds_on_signal_error(self, mock_queue):
        """シグナルが例外を投げても EventDetail の更新は成功する"""
        with patch("twitter.signals.threading.Thread") as mock_thread_cls:
            mock_thread_cls.return_value = MagicMock()
            detail = EventDetail.objects.create(
                event=self.event,
                detail_type="LT",
                status="approved",
                speaker="テスト太郎",
                theme="テスト発表",
                start_time=datetime.time(22, 15),
            )
        detail.theme = "更新された発表"
        detail.save()
        detail.refresh_from_db()
        self.assertEqual(detail.theme, "更新された発表")
