"""自動投稿シグナルのエラー処理テスト。"""

import datetime
from unittest.mock import MagicMock, patch


from community.models import Community
from event.models import EventDetail
from twitter.models import TweetQueue

from twitter.tests._auto_tweet_test_base import AutoTweetTestBase

class SignalErrorHandlingTest(AutoTweetTestBase):
    """シグナルハンドラの例外がメインの保存処理を妨げないことをテスト。

    tweet_queue テーブル未作成時に EventDetail 保存が
    500 エラーになるインシデントの再発防止テスト。
    """

    def setUp(self):
        super().setUp()
        # community を approved にしておく
        with patch("twitter.services.tweet_generation.threading.Thread") as mock_thread_cls:
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
        with patch("twitter.services.tweet_generation.threading.Thread") as mock_thread_cls:
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
