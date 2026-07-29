"""Discord Webhook 共通送信境界のテスト."""

from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase

from website.discord_webhook import post_discord_webhook


class DiscordWebhookTest(SimpleTestCase):
    """共通送信境界の成功判定とリトライを検証する."""

    def setUp(self):
        self.original_sleep = post_discord_webhook.retry.sleep
        post_discord_webhook.retry.sleep = lambda _seconds: None

    def tearDown(self):
        post_discord_webhook.retry.sleep = self.original_sleep

    @patch("website.discord_webhook.requests.post")
    def test_accepts_any_2xx_response_without_retry(self, mock_post):
        """任意の2xx応答を1回で成功とする."""
        for status_code in (200, 201, 204, 299):
            with self.subTest(status_code=status_code):
                mock_post.reset_mock()
                response = MagicMock(status_code=status_code)
                mock_post.return_value = response

                result = post_discord_webhook("https://example.com/webhook", {})

                self.assertIs(result, response)
                mock_post.assert_called_once()

    @patch("website.discord_webhook.requests.post")
    def test_retries_non_2xx_responses_three_times(self, mock_post):
        """3xx・4xx・429・5xx応答を合計3回試行して失敗とする."""
        for status_code in (300, 400, 429, 500):
            with self.subTest(status_code=status_code):
                mock_post.reset_mock()
                mock_post.return_value = MagicMock(status_code=status_code)

                with self.assertRaises(requests.HTTPError) as error:
                    post_discord_webhook("https://example.com/webhook", {})

                self.assertEqual(error.exception.response.status_code, status_code)
                self.assertEqual(mock_post.call_count, 3)

    @patch("website.discord_webhook.requests.post")
    def test_retries_timeout_three_times(self, mock_post):
        """タイムアウトを合計3回試行して最終例外を送出する."""
        mock_post.side_effect = requests.Timeout("timed out")

        with self.assertRaises(requests.Timeout):
            post_discord_webhook("https://example.com/webhook", {})

        self.assertEqual(mock_post.call_count, 3)

    @patch("website.discord_webhook.requests.post")
    def test_retries_connection_error_three_times(self, mock_post):
        """接続エラーを合計3回試行して最終例外を送出する."""
        mock_post.side_effect = requests.ConnectionError("connection failed")

        with self.assertRaises(requests.ConnectionError):
            post_discord_webhook("https://example.com/webhook", {})

        self.assertEqual(mock_post.call_count, 3)

    @patch("website.discord_webhook.requests.post")
    def test_waits_one_then_two_seconds(self, mock_post):
        """最大3試行の間を1秒・2秒待機する."""
        waits = []
        post_discord_webhook.retry.sleep = waits.append
        mock_post.side_effect = requests.ConnectionError("connection failed")

        with self.assertRaises(requests.ConnectionError):
            post_discord_webhook("https://example.com/webhook", {})

        self.assertEqual(waits, [1.0, 2.0])

    @patch("website.discord_webhook.requests.post")
    def test_retry_logs_exclude_webhook_url_and_exception_message(self, mock_post):
        """リトライログはURL・token・例外本文・tracebackを出さない."""
        sensitive_url = "https://discord.com/api/webhooks/123456789/secret-token"
        response = MagicMock(status_code=429)
        mock_post.side_effect = requests.HTTPError(
            f"request failed for {sensitive_url}",
            response=response,
        )

        with self.assertLogs("website.retry", level="WARNING") as log_context:
            with self.assertRaises(requests.HTTPError):
                post_discord_webhook(sensitive_url, {})

        logs = "\n".join(log_context.output)
        self.assertIn("attempt=1/3", logs)
        self.assertIn("error_type=HTTPError", logs)
        self.assertIn("status_code=429", logs)
        self.assertNotIn(sensitive_url, logs)
        self.assertNotIn("secret-token", logs)
        self.assertNotIn("request failed", logs)
        self.assertNotIn("Traceback", logs)
