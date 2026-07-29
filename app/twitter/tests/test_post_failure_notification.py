"""投稿失敗時の Admin Discord Webhook 通知テスト

notify_tweet_post_failure が settings.DISCORD_WEBHOOK_URL 設定下で
ツイート本文を含む正しいペイロードを送信することを検証する。
"""
import datetime
from unittest.mock import MagicMock, patch

import requests
from django.test import TestCase, override_settings

from community.models import Community
from twitter.models import TweetQueue
from twitter.notifications import notify_tweet_post_failure
from twitter.services.tweet_scheduling_service import post_tweet_queue_item
from website.discord_webhook import post_discord_webhook


class NotifyTweetPostFailureTest(TestCase):
    """notify_tweet_post_failure のテスト"""

    def setUp(self):
        self.community = Community.objects.create(
            name="Notify Test Community",
            start_time=datetime.time(21, 0),
            duration=60,
            weekdays=["Mon"],
            frequency="Weekly",
            organizers="Test Organizer",
            description="Test Description",
            platform="All",
            status="pending",
        )
        self.queue_item = TweetQueue.objects.create(
            tweet_type="new_community",
            community=self.community,
            status="failed",
            generated_text="テストツイート本文です\n#VRChat",
        )

    @override_settings(DISCORD_WEBHOOK_URL="")
    @patch("website.discord_webhook.requests.post")
    def test_no_notification_when_webhook_url_not_set(self, mock_post):
        """DISCORD_WEBHOOK_URL 未設定時は通知しない"""
        result = {
            "ok": False, "data": None,
            "status_code": 403, "error_body": "forbidden",
        }
        notify_tweet_post_failure(self.queue_item, result)
        mock_post.assert_not_called()

    @override_settings(DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/test/token")
    @patch("website.discord_webhook.requests.post")
    def test_notification_sent_with_correct_payload(self, mock_post):
        """Webhook URL 設定時は requests.post が正しいペイロードで呼ばれる"""
        mock_response = MagicMock(status_code=204)
        mock_post.return_value = mock_response

        result = {
            "ok": False, "data": None,
            "status_code": 403,
            "error_body": '{"detail":"forbidden"}',
        }
        notify_tweet_post_failure(self.queue_item, result)

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://discord.com/api/webhooks/test/token")
        self.assertIn("json", kwargs)
        self.assertIn("timeout", kwargs)

    @override_settings(DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/test/token")
    @patch("website.discord_webhook.requests.post")
    def test_description_contains_generated_text(self, mock_post):
        """description にツイート本文が含まれる"""
        mock_response = MagicMock(status_code=204)
        mock_post.return_value = mock_response

        result = {
            "ok": False, "data": None,
            "status_code": 403, "error_body": "forbidden",
        }
        notify_tweet_post_failure(self.queue_item, result)

        payload = mock_post.call_args.kwargs["json"]
        description = payload["embeds"][0]["description"]
        self.assertIn("テストツイート本文です", description)
        self.assertIn("#VRChat", description)

    @override_settings(DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/test/token")
    @patch("website.discord_webhook.requests.post")
    def test_fields_contain_status_code_and_error_body(self, mock_post):
        """fields に status_code と error_body と詳細URLが含まれる"""
        mock_response = MagicMock(status_code=204)
        mock_post.return_value = mock_response

        result = {
            "ok": False, "data": None,
            "status_code": 403,
            "error_body": '{"detail":"Forbidden: duplicate content"}',
        }
        notify_tweet_post_failure(self.queue_item, result)

        payload = mock_post.call_args.kwargs["json"]
        fields = payload["embeds"][0]["fields"]

        field_values_by_name = {f["name"]: f["value"] for f in fields}
        self.assertIn("HTTPステータス", field_values_by_name)
        self.assertEqual(field_values_by_name["HTTPステータス"], "403")
        self.assertIn("エラー内容", field_values_by_name)
        self.assertIn("Forbidden", field_values_by_name["エラー内容"])
        self.assertIn("キュー詳細", field_values_by_name)
        # build_site_url 経由で組み立てられる絶対 URL を直接検証する。
        # SITE_URL/APP_CANONICAL_HOST を切り替えたときに壊れないことを保証する。
        from website.constants import build_site_url
        self.assertEqual(
            field_values_by_name["キュー詳細"],
            build_site_url(f"/twitter/queue/{self.queue_item.pk}/"),
        )
        self.assertIn("集会", field_values_by_name)
        self.assertEqual(field_values_by_name["集会"], self.community.name)

    @override_settings(DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/test/token")
    @patch("website.discord_webhook.requests.post")
    def test_long_error_body_is_truncated(self, mock_post):
        """長いエラーボディは 1024 文字制限で切り詰められる"""
        mock_response = MagicMock(status_code=204)
        mock_post.return_value = mock_response

        long_body = "A" * 5000
        result = {
            "ok": False, "data": None,
            "status_code": 500, "error_body": long_body,
        }
        notify_tweet_post_failure(self.queue_item, result)

        payload = mock_post.call_args.kwargs["json"]
        fields = payload["embeds"][0]["fields"]
        error_value = next(f["value"] for f in fields if f["name"] == "エラー内容")
        self.assertLessEqual(len(error_value), 1024)
        self.assertTrue(error_value.endswith("..."))

    @override_settings(DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/test/token")
    @patch("website.discord_webhook.requests.post")
    def test_none_status_code_rendered_as_na(self, mock_post):
        """status_code が None のときは N/A と表示される"""
        mock_response = MagicMock(status_code=204)
        mock_post.return_value = mock_response

        result = {
            "ok": False, "data": None,
            "status_code": None, "error_body": None,
        }
        notify_tweet_post_failure(self.queue_item, result)

        payload = mock_post.call_args.kwargs["json"]
        fields = payload["embeds"][0]["fields"]
        field_values_by_name = {f["name"]: f["value"] for f in fields}
        self.assertEqual(field_values_by_name["HTTPステータス"], "N/A")
        self.assertEqual(field_values_by_name["エラー内容"], "(なし)")

    @override_settings(DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/test/token")
    @patch("website.discord_webhook.requests.post")
    def test_request_exception_does_not_propagate(self, mock_post):
        """最終失敗を安全にlogし、呼び出し元に伝播しない."""
        sensitive_url = "https://discord.com/api/webhooks/123456789/secret-token"
        mock_post.side_effect = Exception(f"network error: {sensitive_url}")

        result = {
            "ok": False, "data": None,
            "status_code": 403, "error_body": "forbidden",
        }
        with self.assertLogs("twitter.notifications", level="ERROR") as log_context:
            notify_tweet_post_failure(self.queue_item, result)

        logs = "\n".join(log_context.output)
        self.assertIn("error_type=Exception", logs)
        self.assertIn("status_code=None", logs)
        self.assertNotIn(sensitive_url, logs)
        self.assertNotIn("secret-token", logs)
        self.assertNotIn("network error", logs)
        self.assertNotIn("Traceback", logs)

    @override_settings(DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/test/token")
    @patch("website.discord_webhook.requests.post")
    def test_final_notification_failure_keeps_saved_queue_state(self, mock_post):
        """通知が最終失敗しても、その前に失敗状態がDBへ保存される."""
        self.queue_item.status = "ready"
        self.queue_item.error_message = ""
        self.queue_item.save(update_fields=["status", "error_message"])
        observed_statuses = []

        def fail_after_reading_saved_state(*_args, **_kwargs):
            saved_queue = TweetQueue.objects.get(pk=self.queue_item.pk)
            observed_statuses.append(saved_queue.status)
            raise requests.ConnectionError("network error")

        mock_post.side_effect = fail_after_reading_saved_state
        original_sleep = post_discord_webhook.retry.sleep
        post_discord_webhook.retry.sleep = lambda _seconds: None
        try:
            result = post_tweet_queue_item(
                self.queue_item,
                post_tweet_func=lambda *_args, **_kwargs: {
                    "ok": False,
                    "data": None,
                    "status_code": 503,
                    "error_body": "service unavailable",
                },
                notify_failure_func=notify_tweet_post_failure,
            )
        finally:
            post_discord_webhook.retry.sleep = original_sleep

        self.queue_item.refresh_from_db()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.queue_item.status, "failed")
        self.assertIn("service unavailable", self.queue_item.error_message)
        self.assertEqual(observed_statuses, ["failed", "failed", "failed"])
