"""投稿本文の非同期生成のテスト。"""

from django.db import DatabaseError
from unittest.mock import patch

from django.test import override_settings

from community.models import Community
from twitter.models import TweetQueue

from twitter.tests._auto_tweet_test_base import AutoTweetTestBase

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

        from twitter.services.tweet_generation import _generate_tweet_async
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

        from twitter.services.tweet_generation import _generate_tweet_async
        _generate_tweet_async(queue_item.pk)

        queue_item.refresh_from_db()
        self.assertEqual(queue_item.status, "generation_failed")
        self.assertIn("テキスト生成に失敗", queue_item.error_message)

    @patch("twitter.tweet_generator.generate_new_community_tweet")
    def test_generate_async_ignores_stale_generation_token(self, mock_generate):
        """古い生成スレッドは現行キューの本文を上書きしない"""
        mock_generate.return_value = "古い告知テキスト"
        queue_item = self._create_queue()
        queue_item.generated_text = "新しい告知テキスト"
        queue_item.status = "ready"
        queue_item.generation_token = "current-token"
        queue_item.save(update_fields=["generated_text", "status", "generation_token"])

        from twitter.services.tweet_generation import _generate_tweet_async
        _generate_tweet_async(queue_item.pk, "stale-token")

        queue_item.refresh_from_db()
        self.assertEqual(queue_item.status, "ready")
        self.assertEqual(queue_item.generated_text, "新しい告知テキスト")
        self.assertEqual(queue_item.generation_token, "current-token")

    @patch("twitter.tweet_generator.generate_new_community_tweet")
    def test_generate_async_exception(self, mock_generate):
        """テキスト生成中に例外が発生した場合 generation_failed になる"""
        mock_generate.side_effect = RuntimeError("LLM API error")
        queue_item = self._create_queue()

        from twitter.services.tweet_generation import _generate_tweet_async
        _generate_tweet_async(queue_item.pk)

        queue_item.refresh_from_db()
        self.assertEqual(queue_item.status, "generation_failed")
        self.assertIn("LLM API error", queue_item.error_message)

    @patch("twitter.services.tweet_generation._save_generation_failure", side_effect=DatabaseError("db write failed"))
    @patch("twitter.tweet_generator.generate_new_community_tweet")
    def test_generate_async_logs_failure_persistence_error(self, mock_generate, mock_save_failure):
        """失敗状態の保存にも失敗した場合は例外情報をログに残す"""
        mock_generate.side_effect = RuntimeError("LLM API error")
        queue_item = self._create_queue()

        from twitter.services.tweet_generation import _generate_tweet_async
        with self.assertLogs("twitter.services.tweet_generation", level="ERROR") as log_ctx:
            _generate_tweet_async(queue_item.pk)

        mock_save_failure.assert_called_once()
        self.assertTrue(
            any(
                "Failed to persist async tweet generation failure" in message
                for message in log_ctx.output
            )
        )

    def test_generate_async_nonexistent_queue(self):
        """存在しないキューIDでもエラーにならない"""
        from twitter.services.tweet_generation import _generate_tweet_async
        # Should not raise
        _generate_tweet_async(99999)

    @patch("django.db.connections.close_all")
    @patch("twitter.tweet_generator.generate_new_community_tweet")
    def test_generate_async_closes_db_connections(self, mock_generate, mock_close_all):
        """バックグラウンド生成の終了時にDB接続を閉じる"""
        mock_generate.return_value = "告知テスト"
        queue_item = self._create_queue()

        from twitter.services.tweet_generation import _generate_tweet_async
        _generate_tweet_async(queue_item.pk)

        mock_close_all.assert_called_once()

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

        from twitter.services.tweet_generation import _generate_tweet_async
        _generate_tweet_async(queue_item.pk)

        queue_item.refresh_from_db()
        self.assertEqual(queue_item.status, "ready")
        self.assertIn("/cdn-cgi/image/width=960", queue_item.image_url)
        self.assertIn("community/1/poster.webp", queue_item.image_url)
