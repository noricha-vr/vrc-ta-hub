"""イベント詳細シグナルのテスト。"""

import datetime
from unittest.mock import MagicMock, patch

from django.utils import timezone

from event.models import Event, EventDetail
from tests.factories import make_event, make_event_detail
from twitter.models import TweetQueue
from twitter.scheduling import default_scheduled_at

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

        self.assertEqual(TweetQueue.objects.count(), 1)
        queue = TweetQueue.objects.get(tweet_type="lt")
        self.assertEqual(queue.tweet_type, "lt")
        self.assertEqual(queue.event_detail, detail)
        self.assertEqual(queue.event, self.event)
        self.assertEqual(queue.status, "generating")
        self.assertEqual(queue.scheduled_at, default_scheduled_at("lt", self.event))
        self.assertEqual(timezone.localtime(queue.scheduled_at).hour, 12)
        self.assertFalse(TweetQueue.objects.filter(tweet_type="daily_reminder").exists())

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

        self.assertEqual(TweetQueue.objects.count(), 1)
        queue = TweetQueue.objects.get(tweet_type="special")
        self.assertEqual(queue.tweet_type, "special")
        self.assertEqual(queue.event_detail, detail)
        self.assertEqual(timezone.localtime(queue.scheduled_at).hour, 12)
        self.assertFalse(TweetQueue.objects.filter(tweet_type="daily_reminder").exists())

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
        self.assertEqual(TweetQueue.objects.count(), 1)
        self.assertFalse(TweetQueue.objects.filter(tweet_type="daily_reminder").exists())

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

        self.assertEqual(TweetQueue.objects.count(), 1)
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
        self.assertFalse(TweetQueue.objects.filter(tweet_type="daily_reminder").exists())

        # 内容変更なしで再保存
        detail.save()
        self.assertEqual(TweetQueue.objects.filter(tweet_type="lt").count(), 1)
        self.assertFalse(TweetQueue.objects.filter(tweet_type="daily_reminder").exists())

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
        self.assertFalse(TweetQueue.objects.filter(tweet_type="daily_reminder").exists())
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

        detail.theme = "VRChatで学ぶRust"
        detail.save()

        self.assertEqual(TweetQueue.objects.filter(tweet_type="lt").count(), 1)
        self.assertFalse(TweetQueue.objects.filter(tweet_type="daily_reminder").exists())
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

        # 投稿済みLT + 新規LT = 2件
        self.assertEqual(TweetQueue.objects.filter(tweet_type="lt").count(), 2)
        self.assertFalse(TweetQueue.objects.filter(tweet_type="daily_reminder").exists())
        self.assertEqual(TweetQueue.objects.filter(status="posted").count(), 1)
        self.assertEqual(TweetQueue.objects.filter(tweet_type="lt", status="generating").count(), 1)

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
        self.assertFalse(TweetQueue.objects.filter(tweet_type="daily_reminder").exists())

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
    def test_today_event_before_start_creates_lt_queue_and_no_daily_reminder(self, mock_thread_cls):
        """当日イベントでも開始前の承認なら個別 LT キューを 1 件作成し、daily_reminder は作らない"""
        mock_thread_cls.return_value = MagicMock()
        today = timezone.localdate()
        before_noon = timezone.make_aware(
            datetime.datetime.combine(today, datetime.time(10, 0)),
            timezone.get_current_timezone(),
        )
        today_event = Event.objects.create(
            community=self.community,
            date=today,
            start_time=datetime.time(22, 0),
            duration=60,
        )
        with (
            patch("twitter.signals.timezone.now", return_value=before_noon),
            patch("twitter.scheduling.timezone.now", return_value=before_noon),
        ):
            detail = EventDetail.objects.create(
                event=today_event,
                detail_type="LT",
                status="approved",
                speaker="テスト太郎",
                theme="当日のLT",
                start_time=datetime.time(22, 15),
            )

        self.assertEqual(TweetQueue.objects.count(), 1)
        lt_queue = TweetQueue.objects.get(tweet_type="lt", event_detail=detail)
        self.assertEqual(lt_queue.status, "generating")
        self.assertEqual(
            timezone.localtime(lt_queue.scheduled_at),
            timezone.make_aware(
                datetime.datetime.combine(today, datetime.time(12, 0)),
                timezone.get_current_timezone(),
            ),
        )
        self.assertFalse(TweetQueue.objects.filter(tweet_type="daily_reminder").exists())
        mock_thread_cls.assert_called_once()

    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_today_event_after_noon_before_start_schedules_immediately(self, mock_thread_cls):
        """当日 12:00 以降〜開始前の承認では翌日に送らず即時予定でキューを作成する"""
        mock_thread_cls.return_value = MagicMock()
        today = timezone.localdate()
        after_noon = timezone.make_aware(
            datetime.datetime.combine(today, datetime.time(15, 30)),
            timezone.get_current_timezone(),
        )
        today_event = Event.objects.create(
            community=self.community,
            date=today,
            start_time=datetime.time(22, 0),
            duration=60,
        )
        with (
            patch("twitter.signals.timezone.now", return_value=after_noon),
            patch("twitter.scheduling.timezone.now", return_value=after_noon),
        ):
            detail = EventDetail.objects.create(
                event=today_event,
                detail_type="LT",
                status="approved",
                speaker="テスト太郎",
                theme="当日午後承認のLT",
                start_time=datetime.time(22, 15),
            )

        lt_queue = TweetQueue.objects.get(tweet_type="lt", event_detail=detail)
        self.assertEqual(lt_queue.scheduled_at, after_noon)
        self.assertFalse(TweetQueue.objects.filter(tweet_type="daily_reminder").exists())

    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_today_event_after_start_does_not_create_queue(self, mock_thread_cls):
        """当日イベントの開始時刻を過ぎた後の承認ではキューを作成しない"""
        mock_thread_cls.return_value = MagicMock()
        today = timezone.localdate()
        after_start = timezone.make_aware(
            datetime.datetime.combine(today, datetime.time(22, 5)),
            timezone.get_current_timezone(),
        )
        today_event = Event.objects.create(
            community=self.community,
            date=today,
            start_time=datetime.time(22, 0),
            duration=60,
        )
        with patch("twitter.signals.timezone.now", return_value=after_start):
            EventDetail.objects.create(
                event=today_event,
                detail_type="LT",
                status="approved",
                speaker="テスト太郎",
                theme="開始後のLT",
                start_time=datetime.time(22, 15),
            )

        self.assertEqual(TweetQueue.objects.count(), 0)
        mock_thread_cls.assert_not_called()

    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_today_event_theme_change_regenerates_lt_queue(self, mock_thread_cls):
        """当日開始前の LT 内容変更では未投稿の lt キューを再生成する"""
        mock_thread_cls.return_value = MagicMock()
        today = timezone.localdate()
        before_start = timezone.make_aware(
            datetime.datetime.combine(today, datetime.time(15, 0)),
            timezone.get_current_timezone(),
        )
        today_event = Event.objects.create(
            community=self.community,
            date=today,
            start_time=datetime.time(22, 0),
            duration=60,
        )
        with (
            patch("twitter.signals.timezone.now", return_value=before_start),
            patch("twitter.scheduling.timezone.now", return_value=before_start),
        ):
            detail = EventDetail.objects.create(
                event=today_event,
                detail_type="LT",
                status="approved",
                speaker="テスト太郎",
                theme="当日のLT",
                start_time=datetime.time(22, 15),
            )
            old_queue = TweetQueue.objects.get(tweet_type="lt", event_detail=detail)
            old_queue.generated_text = "告知 v1"
            old_queue.status = "ready"
            old_queue.save(update_fields=["generated_text", "status"])

            detail.theme = "更新後の当日LT"
            detail.save()

        new_queue = TweetQueue.objects.get(tweet_type="lt", event_detail=detail)
        self.assertNotEqual(new_queue.pk, old_queue.pk)
        self.assertEqual(new_queue.status, "generating")
        self.assertFalse(TweetQueue.objects.filter(tweet_type="daily_reminder").exists())

    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_delete_event_detail_does_not_create_daily_reminder(self, mock_thread_cls):
        """当日発表の承認・削除を通じても daily_reminder は作成されない"""
        mock_thread_cls.return_value = MagicMock()
        today = timezone.localdate()
        before_start = timezone.make_aware(
            datetime.datetime.combine(today, datetime.time(15, 0)),
            timezone.get_current_timezone(),
        )
        today_event = make_event(
            self.community,
            event_date=today,
            start_time=datetime.time(22, 0),
            duration=60,
        )
        with (
            patch("twitter.signals.timezone.now", return_value=before_start),
            patch("twitter.scheduling.timezone.now", return_value=before_start),
        ):
            detail = EventDetail.objects.create(
                event=today_event,
                detail_type="LT",
                status="approved",
                speaker="テスト太郎",
                theme="当日のLT",
                start_time=datetime.time(22, 15),
            )
            detail.delete()

        self.assertFalse(TweetQueue.objects.filter(tweet_type="daily_reminder").exists())

    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_unapproved_rejected_deleted_or_type_changed_detail_cancels_unposted_queue(self, mock_thread_cls):
        """承認取り消し・却下・論理削除・種別変更で未投稿キューが消え、再承認やSPECIAL変更で再作成される"""
        mock_thread_cls.return_value = MagicMock()
        today = timezone.localdate()
        before_start = timezone.make_aware(
            datetime.datetime.combine(today, datetime.time(15, 0)),
            timezone.get_current_timezone(),
        )
        today_event = make_event(
            self.community,
            event_date=today,
            start_time=datetime.time(22, 0),
            duration=60,
        )
        with (
            patch("twitter.signals.timezone.now", return_value=before_start),
            patch("twitter.scheduling.timezone.now", return_value=before_start),
        ):
            detail = make_event_detail(
                today_event,
                detail_type="LT",
                status="approved",
                speaker="太郎",
                theme="発表",
                start_time=datetime.time(22, 15),
            )
            self.assertEqual(TweetQueue.objects.filter(event_detail=detail, tweet_type="lt").count(), 1)

            # 1) approved -> pending で未投稿キューを取り消し、再承認で再作成される
            detail.status = "pending"
            detail.save()
            self.assertEqual(TweetQueue.objects.filter(event_detail=detail).count(), 0)
            detail.status = "approved"
            detail.save()
            self.assertEqual(TweetQueue.objects.filter(event_detail=detail, tweet_type="lt").count(), 1)

            # 2) approved -> rejected で未投稿キューを取り消す
            detail.status = "rejected"
            detail.save()
            self.assertEqual(TweetQueue.objects.filter(event_detail=detail).count(), 0)

            # 3) 再承認後に LT -> SPECIAL へ変更すると lt が消え special が作られる
            detail.status = "approved"
            detail.save()
            detail.detail_type = "SPECIAL"
            detail.save()
            self.assertEqual(TweetQueue.objects.filter(event_detail=detail, tweet_type="lt").count(), 0)
            self.assertEqual(TweetQueue.objects.filter(event_detail=detail, tweet_type="special").count(), 1)

            # 4) BLOG へ変更すると未投稿キューを取り消す
            detail.detail_type = "BLOG"
            detail.save()
            self.assertEqual(TweetQueue.objects.filter(event_detail=detail).count(), 0)

            # 5) 論理削除でも未投稿キューを取り消す
            detail.detail_type = "LT"
            detail.save()
            self.assertEqual(TweetQueue.objects.filter(event_detail=detail, tweet_type="lt").count(), 1)
            detail.delete()
            self.assertEqual(TweetQueue.objects.filter(event_detail=detail).count(), 0)

    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_started_skip_queue_is_cleaned_on_unapprove_and_requeued_on_reapprove(self, mock_thread_cls):
        """開始済みスキップ後の承認取消で skipped キューも削除され、未来日移動と再承認で告知が復帰する"""
        from twitter.services.announcement_eligibility import EVENT_STARTED_SKIP_MESSAGE

        mock_thread_cls.return_value = MagicMock()
        today = timezone.localdate()
        before_start = timezone.make_aware(
            datetime.datetime.combine(today, datetime.time(15, 0)),
            timezone.get_current_timezone(),
        )
        today_event = make_event(
            self.community,
            event_date=today,
            start_time=datetime.time(22, 0),
            duration=60,
        )
        with (
            patch("twitter.signals.timezone.now", return_value=before_start),
            patch("twitter.scheduling.timezone.now", return_value=before_start),
        ):
            detail = make_event_detail(
                today_event,
                detail_type="LT",
                status="approved",
                speaker="再承認太郎",
                theme="復帰テスト発表",
                start_time=datetime.time(22, 15),
            )
            queue = TweetQueue.objects.get(event_detail=detail, tweet_type="lt")
            queue.status = "skipped"
            queue.error_message = EVENT_STARTED_SKIP_MESSAGE
            queue.save(update_fields=["status", "error_message"])

            detail.status = "pending"
            detail.save()
            self.assertFalse(TweetQueue.objects.filter(event_detail=detail).exists())

            today_event.date = today + datetime.timedelta(days=2)
            today_event.save(update_fields=["date"])
            detail.status = "approved"
            detail.save()

        recreated = TweetQueue.objects.get(event_detail=detail, tweet_type="lt")
        self.assertEqual(recreated.status, "generating")

    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_retraction_and_edit_preserve_misskey_delivery_history_without_duplicates(self, mock_thread_cls):
        """Misskey 投稿済み/結果不明のキューは承認取消で削除せず skipped 保持し、再承認・内容変更でも再利用する"""
        from twitter.models import MisskeyNote
        from twitter.services.announcement_eligibility import RETRACTED_PRESENTATION_SKIP_MESSAGE

        mock_thread_cls.return_value = MagicMock()
        today = timezone.localdate()
        before_start = timezone.make_aware(
            datetime.datetime.combine(today, datetime.time(15, 0)),
            timezone.get_current_timezone(),
        )
        today_event = make_event(
            self.community,
            event_date=today,
            start_time=datetime.time(22, 0),
            duration=60,
        )

        for mk_status, extra in (
            ("posted", {"note_id": "mk-posted-1", "posted_at": before_start}),
            ("unknown", {"error_message": "timeout"}),
        ):
            with (
                self.subTest(misskey_status=mk_status),
                patch("twitter.signals.timezone.now", return_value=before_start),
                patch("twitter.scheduling.timezone.now", return_value=before_start),
            ):
                detail = make_event_detail(
                    today_event,
                    detail_type="LT",
                    status="approved",
                    speaker=f"太郎-{mk_status}",
                    theme="初期テーマ",
                    start_time=datetime.time(22, 15),
                )
                queue = TweetQueue.objects.get(event_detail=detail, tweet_type="lt")
                queue.status = "ready"
                queue.generated_text = "告知 v1"
                queue.save(update_fields=["status", "generated_text"])
                note = MisskeyNote.objects.create(
                    tweet_queue=queue,
                    status=mk_status,
                    **extra,
                )

                # 1) 承認取り消しで CASCADE 削除せず skipped 保持し、MisskeyNote 履歴を維持する
                detail.status = "pending"
                detail.save()
                queue.refresh_from_db()
                note.refresh_from_db()
                self.assertEqual(queue.status, "skipped")
                self.assertEqual(queue.error_message, RETRACTED_PRESENTATION_SKIP_MESSAGE)
                self.assertEqual(note.status, mk_status)

                # 2) 再承認しても新キューを作らず、同じ TweetQueue を生成中へ戻して MisskeyNote を維持する
                detail.status = "approved"
                detail.save()
                self.assertEqual(TweetQueue.objects.filter(event_detail=detail, tweet_type="lt").count(), 1)
                queue.refresh_from_db()
                note.refresh_from_db()
                self.assertEqual(queue.status, "generating")
                self.assertEqual(queue.generated_text, "")
                self.assertEqual(note.status, mk_status)

                # 3) X がまだ ready のまま発表内容を編集しても、同一 TweetQueue を再利用し MisskeyNote を消さない
                queue.status = "ready"
                queue.generated_text = "告知 v2"
                queue.save(update_fields=["status", "generated_text"])
                detail.theme = "更新後テーマ"
                detail.save()
                self.assertEqual(TweetQueue.objects.filter(event_detail=detail, tweet_type="lt").count(), 1)
                queue.refresh_from_db()
                note.refresh_from_db()
                self.assertEqual(queue.status, "generating")
                self.assertEqual(queue.generated_text, "")
                self.assertEqual(note.status, mk_status)

    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_soft_deleted_detail_restore_and_bulk_restore_sync_presentation_queues(self, mock_thread_cls):
        """soft_delete 後の単体 restore() と一括 queryset.restore() で発表告知キューと MisskeyNote が復帰する"""
        from twitter.models import MisskeyNote
        from twitter.services.announcement_eligibility import RETRACTED_PRESENTATION_SKIP_MESSAGE

        mock_thread_cls.return_value = MagicMock()
        today = timezone.localdate()
        before_start = timezone.make_aware(
            datetime.datetime.combine(today, datetime.time(15, 0)),
            timezone.get_current_timezone(),
        )
        today_event = make_event(
            self.community,
            event_date=today,
            start_time=datetime.time(22, 0),
            duration=60,
        )

        with (
            patch("twitter.signals.timezone.now", return_value=before_start),
            patch("twitter.scheduling.timezone.now", return_value=before_start),
            patch("event.models.timezone.now", return_value=before_start),
        ):
            # 1) Misskey 履歴なしの soft_delete -> restore() で新規キューが作られる
            d1 = make_event_detail(
                today_event,
                detail_type="LT",
                status="approved",
                speaker="復元太郎",
                theme="単体復元",
                start_time=datetime.time(22, 10),
            )
            q1 = TweetQueue.objects.get(event_detail=d1, tweet_type="lt")
            d1.soft_delete()
            self.assertFalse(TweetQueue.objects.filter(event_detail=d1).exists())
            d1.restore()
            q1_restored = TweetQueue.objects.get(event_detail=d1, tweet_type="lt")
            self.assertNotEqual(q1_restored.pk, q1.pk)
            self.assertEqual(q1_restored.status, "generating")

            # 2) Misskey posted 保護付きの soft_delete -> restore() で同一キュー再利用・履歴保持
            d2 = make_event_detail(
                today_event,
                detail_type="LT",
                status="approved",
                speaker="履歴保持花子",
                theme="履歴保持復元",
                start_time=datetime.time(22, 20),
            )
            q2 = TweetQueue.objects.get(event_detail=d2, tweet_type="lt")
            q2.status = "ready"
            q2.generated_text = "元の告知"
            q2.save(update_fields=["status", "generated_text"])
            n2 = MisskeyNote.objects.create(tweet_queue=q2, status="posted", note_id="mk-restore-1")
            d2.soft_delete()
            q2.refresh_from_db()
            self.assertEqual(q2.status, "skipped")
            d2.restore()
            q2.refresh_from_db()
            n2.refresh_from_db()
            self.assertEqual(TweetQueue.objects.filter(event_detail=d2, tweet_type="lt").count(), 1)
            self.assertEqual(q2.status, "generating")
            self.assertEqual(n2.status, "posted")
            self.assertEqual(n2.note_id, "mk-restore-1")

            # 3) バルク削除後に配信処理で skipped されたキューも queryset.restore() で重複なく復帰する
            d3 = make_event_detail(
                today_event,
                detail_type="LT",
                status="approved",
                speaker="一括復元次郎",
                theme="バルク復元テスト",
                start_time=datetime.time(22, 30),
            )
            q3 = TweetQueue.objects.get(event_detail=d3, tweet_type="lt")
            q3.status = "skipped"
            q3.error_message = RETRACTED_PRESENTATION_SKIP_MESSAGE
            q3.generated_text = ""
            q3.save(update_fields=["status", "error_message", "generated_text"])
            n3 = MisskeyNote.objects.create(tweet_queue=q3, status="skipped", error_message="canceled", attempts=1)
            EventDetail.objects.filter(pk=d3.pk).delete()
            restored_count = EventDetail.all_objects.filter(pk=d3.pk).restore()
            self.assertEqual(restored_count, 1)
            q3.refresh_from_db()
            n3.refresh_from_db()
            self.assertEqual(TweetQueue.objects.filter(event_detail=d3, tweet_type="lt").count(), 1)
            self.assertEqual(q3.status, "generating")
            self.assertEqual(n3.status, "pending")
            self.assertEqual(n3.attempts, 0)

            # 4) バルク削除のみでキュー消去されていた場合も queryset.restore() で新規作成される
            d4 = make_event_detail(
                today_event,
                detail_type="LT",
                status="approved",
                speaker="一括新規四郎",
                theme="バルク新規テスト",
                start_time=datetime.time(22, 40),
            )
            TweetQueue.objects.filter(event_detail=d4).delete()
            EventDetail.objects.filter(pk=d4.pk).delete()
            EventDetail.all_objects.filter(pk=d4.pk).restore()
            q4_new = TweetQueue.objects.get(event_detail=d4, tweet_type="lt")
            self.assertEqual(q4_new.status, "generating")
