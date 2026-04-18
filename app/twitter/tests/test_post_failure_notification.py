"""投稿失敗時の Admin Discord Webhook 通知テスト

notify_tweet_post_failure が settings.DISCORD_WEBHOOK_URL 設定下で
ツイート本文を含む正しいペイロードを送信することを検証する。
"""
import datetime
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from community.models import Community
from twitter.models import TweetQueue
from twitter.notifications import notify_tweet_post_failure


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
    @patch("twitter.notifications.requests.post")
    def test_no_notification_when_webhook_url_not_set(self, mock_post):
        """DISCORD_WEBHOOK_URL 未設定時は通知しない"""
        result = {
            "ok": False, "data": None,
            "status_code": 403, "error_body": "forbidden",
        }
        notify_tweet_post_failure(self.queue_item, result)
        mock_post.assert_not_called()

    @override_settings(DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/test/token")
    @patch("twitter.notifications.requests.post")
    def test_notification_sent_with_correct_payload(self, mock_post):
        """Webhook URL 設定時は requests.post が正しいペイロードで呼ばれる"""
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 204
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
    @patch("twitter.notifications.requests.post")
    def test_description_contains_generated_text(self, mock_post):
        """description にツイート本文が含まれる"""
        mock_response = MagicMock()
        mock_response.ok = True
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
    @patch("twitter.notifications.requests.post")
    def test_fields_contain_status_code_and_error_body(self, mock_post):
        """fields に status_code と error_body と詳細URLが含まれる"""
        mock_response = MagicMock()
        mock_response.ok = True
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
        self.assertIn(
            f"/twitter/queue/{self.queue_item.pk}/",
            field_values_by_name["キュー詳細"],
        )
        self.assertIn("集会", field_values_by_name)
        self.assertEqual(field_values_by_name["集会"], self.community.name)

    @override_settings(DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/test/token")
    @patch("twitter.notifications.requests.post")
    def test_long_error_body_is_truncated(self, mock_post):
        """長いエラーボディは 1024 文字制限で切り詰められる"""
        mock_response = MagicMock()
        mock_response.ok = True
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
    @patch("twitter.notifications.requests.post")
    def test_none_status_code_rendered_as_na(self, mock_post):
        """status_code が None のときは N/A と表示される"""
        mock_response = MagicMock()
        mock_response.ok = True
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
    @patch("twitter.notifications.requests.post")
    def test_request_exception_does_not_propagate(self, mock_post):
        """requests.post が例外を投げても呼び出し元に伝播しない"""
        mock_post.side_effect = Exception("network error")

        result = {
            "ok": False, "data": None,
            "status_code": 403, "error_body": "forbidden",
        }
        # 例外が外に伝播しないことを確認
        notify_tweet_post_failure(self.queue_item, result)
