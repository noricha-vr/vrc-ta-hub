"""集会承認シグナルのテスト。"""

from unittest.mock import MagicMock, patch

from django.utils import timezone

from twitter.models import TweetQueue

from twitter.tests._auto_tweet_test_base import AutoTweetTestBase

class CommunityApprovalSignalTest(AutoTweetTestBase):
    """Community 承認時のシグナルテスト"""

    @patch("twitter.services.tweet_generation.threading.Thread")
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
        self.assertEqual(timezone.localtime(queue.scheduled_at).hour, 12)

        # スレッドが起動されたことを確認
        mock_thread_cls.assert_called_once()
        mock_thread.start.assert_called_once()

    @patch("twitter.services.tweet_generation.start_tweet_generation")
    def test_definition_patch_reaches_signal_handler(self, mock_start):
        """定義元の生成開始 patch が signals の呼び出しへ届く。"""
        self.community.status = "approved"
        self.community.save()

        queue = TweetQueue.objects.get(tweet_type="new_community")
        mock_start.assert_called_once_with(queue)

    @patch("twitter.services.tweet_generation.threading.Thread")
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

    @patch("twitter.services.tweet_generation.threading.Thread")
    def test_rejected_community_does_not_create_queue(self, mock_thread_cls):
        """rejected への変更ではキューは作成されない"""
        self.community.status = "rejected"
        self.community.save()

        self.assertEqual(TweetQueue.objects.count(), 0)

    @patch("twitter.services.tweet_generation.threading.Thread")
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
