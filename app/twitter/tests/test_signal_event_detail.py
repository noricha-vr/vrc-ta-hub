"""イベント詳細シグナルのテスト。"""

import datetime
from unittest.mock import MagicMock, patch

from django.utils import timezone

from event.models import Event, EventDetail
from twitter.models import TweetQueue
from twitter.scheduling import default_scheduled_at, scheduled_at_for_date

from twitter.tests._auto_tweet_test_base import AutoTweetTestBase

class EventDetailSignalTest(AutoTweetTestBase):
    """EventDetail 作成/承認時のシグナルテスト"""

    def setUp(self):
        super().setUp()
        # community を approved にしておく (LT テスト用)
        with patch("twitter.services.tweet_generation.threading.Thread") as mock_thread_cls:
            mock_thread_cls.return_value = MagicMock()
            self.community.status = "approved"
            self.community.save()
        # community 承認時のキューをクリア
        TweetQueue.objects.all().delete()

    @patch("twitter.services.tweet_generation.threading.Thread")
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
        self.assertEqual(timezone.localtime(queue.scheduled_at).hour, 12)
        self.assertEqual(reminder.event, self.event)
        self.assertEqual(reminder.scheduled_at, scheduled_at_for_date(self.event.date))
        self.assertEqual(timezone.localtime(reminder.scheduled_at).hour, 19)

    @patch("twitter.services.tweet_generation.threading.Thread")
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
        self.assertEqual(timezone.localtime(queue.scheduled_at).hour, 12)
        self.assertEqual(reminder.scheduled_at, scheduled_at_for_date(self.event.date))

    @patch("twitter.services.tweet_generation.threading.Thread")
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

    @patch("twitter.services.tweet_generation.threading.Thread")
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

    @patch("twitter.services.tweet_generation.threading.Thread")
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

    @patch("twitter.services.tweet_generation.threading.Thread")
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

    @patch("twitter.services.tweet_generation.threading.Thread")
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

    @patch("twitter.services.tweet_generation.threading.Thread")
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

    @patch("twitter.services.tweet_generation.threading.Thread")
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

    @patch("twitter.services.tweet_generation.threading.Thread")
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
        self.assertEqual(TweetQueue.objects.filter(tweet_type="lt", status="generating").count(), 1)
        self.assertEqual(TweetQueue.objects.filter(tweet_type="daily_reminder", status="generating").count(), 1)

    @patch("twitter.services.tweet_generation.threading.Thread")
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

    @patch("twitter.services.tweet_generation.threading.Thread")
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

    @patch("twitter.services.tweet_generation.threading.Thread")
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

    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_today_event_creates_skipped_lt_and_daily_reminder_queue(self, mock_thread_cls):
        """当日のイベントでは個別告知は skipped、daily_reminder が非同期生成される"""
        mock_thread_cls.return_value = MagicMock()

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
        self.assertEqual(reminder_queue.status, "generating")
        self.assertEqual(reminder_queue.generated_text, "")
        self.assertEqual(reminder_queue.scheduled_at, scheduled_at_for_date(today_event.date))
        self.assertTrue(reminder_queue.generation_token)
        mock_thread_cls.assert_called_once()
        self.assertEqual(mock_thread_cls.call_args.kwargs["args"], (reminder_queue.pk, reminder_queue.generation_token))
        mock_thread_cls.return_value.start.assert_called_once()

    @patch("twitter.services.tweet_generation.start_tweet_generation")
    def test_definition_patch_reaches_daily_reminder_sync(self, mock_start):
        """定義元の生成開始 patch が daily_reminder の呼び出しへ届く。"""
        today_event = Event.objects.create(
            community=self.community,
            date=timezone.localdate(),
            start_time=datetime.time(22, 0),
            duration=60,
        )
        EventDetail.objects.create(
            event=today_event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="当日のLT",
            start_time=datetime.time(22, 15),
        )

        reminder_queue = TweetQueue.objects.get(tweet_type="daily_reminder", event=today_event)
        mock_start.assert_called_once_with(reminder_queue)

    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_today_event_theme_change_regenerates_same_daily_reminder_queue(self, mock_thread_cls):
        """当日の LT 内容変更では same-day daily_reminder を同じキューIDのまま非同期再生成する"""
        mock_thread_cls.return_value = MagicMock()

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
        reminder_queue.generated_text = "今日開催のリマインド v1"
        reminder_queue.status = "ready"
        reminder_queue.save(update_fields=["generated_text", "status"])

        detail.theme = "更新後の当日LT"
        detail.save()

        reminder_queue.refresh_from_db()
        self.assertEqual(reminder_queue.status, "generating")
        self.assertEqual(reminder_queue.generated_text, "")
        self.assertEqual(TweetQueue.objects.filter(tweet_type="daily_reminder", event=today_event).count(), 1)
        self.assertEqual(TweetQueue.objects.get(tweet_type="daily_reminder", event=today_event).pk, reminder_queue.pk)
        self.assertEqual(mock_thread_cls.call_count, 2)
        self.assertEqual(mock_thread_cls.call_args.kwargs["args"], (reminder_queue.pk, reminder_queue.generation_token))

    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_future_event_start_time_change_regenerates_daily_reminder_queue(self, mock_thread_cls):
        """future イベントの開始時刻変更でも daily_reminder を同じキューIDのまま非同期再生成する"""
        mock_thread_cls.return_value = MagicMock()

        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="VRChatで学ぶPython",
            start_time=datetime.time(22, 15),
        )
        reminder_queue = TweetQueue.objects.get(tweet_type="daily_reminder", event=self.event)
        reminder_queue.generated_text = "future reminder v1"
        reminder_queue.status = "ready"
        reminder_queue.save(update_fields=["generated_text", "status"])

        detail.start_time = datetime.time(22, 30)
        detail.save()

        reminder_queue.refresh_from_db()
        self.assertEqual(reminder_queue.status, "generating")
        self.assertEqual(reminder_queue.generated_text, "")
        self.assertEqual(TweetQueue.objects.filter(tweet_type="daily_reminder", event=self.event).count(), 1)
        self.assertEqual(mock_thread_cls.call_count, 3)
        self.assertEqual(mock_thread_cls.call_args.kwargs["args"], (reminder_queue.pk, reminder_queue.generation_token))

    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_today_event_unapprove_skips_daily_reminder(self, mock_thread_cls):
        """当日の approved 発表がなくなったら daily_reminder は skipped になる"""
        mock_thread_cls.return_value = MagicMock()

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
