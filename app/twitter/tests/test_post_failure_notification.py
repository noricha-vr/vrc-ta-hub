"""投稿失敗時の Admin Discord Webhook 通知テスト

notify_tweet_post_failure が settings.DISCORD_WEBHOOK_URL 設定下で
ツイート本文を含む正しいペイロードを送信することを検証する。
"""
import datetime
from unittest.mock import MagicMock, patch

import requests
from django.db import OperationalError
from django.test import TestCase, override_settings

from community.models import Community
from twitter.models import TweetQueue
from twitter.notifications import notify_tweet_post_failure
from twitter.services.tweet_scheduling_service import post_tweet_queue_item

MYSQL_RETRY_ERROR_CODES = (2006, 2013)
MYSQL_NON_RETRY_ERROR_CODE = 1048


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
    @patch("twitter.notifications.post_discord_webhook")
    def test_no_notification_when_webhook_url_not_set(self, mock_post):
        """DISCORD_WEBHOOK_URL 未設定時は通知しない"""
        result = {
            "ok": False, "data": None,
            "status_code": 403, "error_body": "forbidden",
        }
        notify_tweet_post_failure(self.queue_item, result)
        mock_post.assert_not_called()

    @override_settings(DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/test/token")
    @patch("twitter.notifications.post_discord_webhook")
    def test_notification_sent_with_correct_payload(self, mock_post):
        """Webhook URL 設定時はgatewayが正しいペイロードで呼ばれる."""
        mock_response = MagicMock(status_code=204)
        mock_post.return_value = mock_response

        result = {
            "ok": False, "data": None,
            "status_code": 403,
            "error_body": '{"detail":"forbidden"}',
        }
        notify_tweet_post_failure(self.queue_item, result)

        mock_post.assert_called_once()
        args = mock_post.call_args.args
        self.assertEqual(args[0], "https://discord.com/api/webhooks/test/token")
        self.assertIn("embeds", args[1])

    @override_settings(DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/test/token")
    @patch("twitter.notifications.post_discord_webhook")
    def test_description_contains_generated_text(self, mock_post):
        """description にツイート本文が含まれる"""
        mock_response = MagicMock(status_code=204)
        mock_post.return_value = mock_response

        result = {
            "ok": False, "data": None,
            "status_code": 403, "error_body": "forbidden",
        }
        notify_tweet_post_failure(self.queue_item, result)

        payload = mock_post.call_args.args[1]
        description = payload["embeds"][0]["description"]
        self.assertIn("テストツイート本文です", description)
        self.assertIn("#VRChat", description)

    @override_settings(DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/test/token")
    @patch("twitter.notifications.post_discord_webhook")
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

        payload = mock_post.call_args.args[1]
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
    @patch("twitter.notifications.post_discord_webhook")
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

        payload = mock_post.call_args.args[1]
        fields = payload["embeds"][0]["fields"]
        error_value = next(f["value"] for f in fields if f["name"] == "エラー内容")
        self.assertLessEqual(len(error_value), 1024)
        self.assertTrue(error_value.endswith("..."))

    @override_settings(DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/test/token")
    @patch("twitter.notifications.post_discord_webhook")
    def test_none_status_code_rendered_as_na(self, mock_post):
        """status_code が None のときは N/A と表示される"""
        mock_response = MagicMock(status_code=204)
        mock_post.return_value = mock_response

        result = {
            "ok": False, "data": None,
            "status_code": None, "error_body": None,
        }
        notify_tweet_post_failure(self.queue_item, result)

        payload = mock_post.call_args.args[1]
        fields = payload["embeds"][0]["fields"]
        field_values_by_name = {f["name"]: f["value"] for f in fields}
        self.assertEqual(field_values_by_name["HTTPステータス"], "N/A")
        self.assertEqual(field_values_by_name["エラー内容"], "(なし)")

    @override_settings(DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/test/token")
    @patch("twitter.notifications.post_discord_webhook")
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
    @patch("twitter.notifications.post_discord_webhook")
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

        self.queue_item.refresh_from_db()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.queue_item.status, "failed")
        self.assertIn("service unavailable", self.queue_item.error_message)
        self.assertEqual(observed_statuses, ["failed"])

    def test_success_state_is_persisted_by_application_service(self):
        """成功時もapplication serviceが投稿結果をDBへ保存する."""
        result = post_tweet_queue_item(
            self.queue_item,
            post_tweet_func=lambda *_args, **_kwargs: {
                "ok": True,
                "data": {"id": "posted-by-service"},
                "status_code": None,
                "error_body": None,
            },
        )

        self.queue_item.refresh_from_db()
        self.assertEqual(result["status"], "posted")
        self.assertEqual(self.queue_item.status, "posted")
        self.assertEqual(self.queue_item.tweet_id, "posted-by-service")
        self.assertIsNotNone(self.queue_item.posted_at)
        self.assertEqual(self.queue_item.error_message, "")

    def _flaky_save(self, error_code, update_fields_history):
        """初回だけ接続断にし、2回目は実DBへ保存するsaveを返す."""
        real_save = self.queue_item.save

        def save(*args, **kwargs):
            update_fields = kwargs.get("update_fields")
            update_fields_history.append(tuple(update_fields or ()))
            if len(update_fields_history) == 1:
                raise OperationalError(error_code, "lost database connection")
            return real_save(*args, **kwargs)

        return save

    def _reset_queue_state(self, *, status="ready", error_message="previous error"):
        """再接続ケースごとに永続化済みキューを初期状態へ戻す."""
        TweetQueue.objects.filter(pk=self.queue_item.pk).update(
            status=status,
            tweet_id="",
            posted_at=None,
            error_message=error_message,
        )
        self.queue_item.refresh_from_db()

    def _run_failure_retry_case(self, error_code, *, failure_status):
        """接続断後に失敗状態を保存し、通知時点のDB状態を返す."""
        self._reset_queue_state(status="ready", error_message="")
        update_fields_history = []
        notified_states = []
        flaky_save = self._flaky_save(error_code, update_fields_history)

        def observe_saved_state(_queue_item, _result):
            saved = TweetQueue.objects.get(pk=self.queue_item.pk)
            notified_states.append((saved.status, saved.error_message))

        with patch.object(self.queue_item, "save", side_effect=flaky_save):
            result = post_tweet_queue_item(
                self.queue_item,
                failure_status=failure_status,
                post_tweet_func=lambda *_args, **_kwargs: {
                    "ok": False,
                    "data": None,
                    "status_code": 503,
                    "error_body": "service unavailable",
                },
                notify_failure_func=observe_saved_state,
            )

        saved_queue = TweetQueue.objects.get(pk=self.queue_item.pk)
        return result, saved_queue, notified_states, update_fields_history

    @patch("twitter.db.connections.close_all")
    def test_success_save_retries_lost_connection_and_persists_fields(
        self,
        mock_close_all,
    ):
        """成功保存はMySQL 2006/2013を再試行して全結果フィールドを永続化する."""
        expected_fields = ("status", "tweet_id", "posted_at", "error_message")
        for error_code in MYSQL_RETRY_ERROR_CODES:
            with self.subTest(error_code=error_code):
                self._reset_queue_state()
                update_fields_history = []
                notifier = MagicMock()
                flaky_save = self._flaky_save(error_code, update_fields_history)

                with patch.object(self.queue_item, "save", side_effect=flaky_save):
                    result = post_tweet_queue_item(
                        self.queue_item,
                        post_tweet_func=lambda *_args, **_kwargs: {
                            "ok": True,
                            "data": {"id": f"tweet-{error_code}"},
                            "status_code": None,
                            "error_body": None,
                        },
                        notify_failure_func=notifier,
                    )

                saved_queue = TweetQueue.objects.get(pk=self.queue_item.pk)
                self.assertEqual(result["status"], "posted")
                self.assertEqual(saved_queue.status, "posted")
                self.assertEqual(saved_queue.tweet_id, f"tweet-{error_code}")
                self.assertIsNotNone(saved_queue.posted_at)
                self.assertEqual(saved_queue.error_message, "")
                self.assertEqual(
                    update_fields_history,
                    [expected_fields, expected_fields],
                )
                notifier.assert_not_called()
        self.assertEqual(mock_close_all.call_count, 2)

    @patch("twitter.db.connections.close_all")
    def test_failure_save_retries_before_notification(
        self,
        mock_close_all,
    ):
        """失敗保存はMySQL 2006/2013を再試行し、保存後に通知する."""
        expected_fields = ("error_message", "status")
        for error_code in MYSQL_RETRY_ERROR_CODES:
            with self.subTest(error_code=error_code):
                result, saved_queue, notified_states, fields = (
                    self._run_failure_retry_case(
                        error_code,
                        failure_status="failed",
                    )
                )
                self.assertEqual(result["status"], "failed")
                self.assertEqual(saved_queue.status, "failed")
                self.assertIn("service unavailable", saved_queue.error_message)
                self.assertEqual(
                    notified_states,
                    [("failed", saved_queue.error_message)],
                )
                self.assertEqual(
                    fields,
                    [expected_fields, expected_fields],
                )
        self.assertEqual(mock_close_all.call_count, 2)

    @patch("twitter.db.connections.close_all")
    def test_failure_without_status_change_retries_and_preserves_status(
        self,
        mock_close_all,
    ):
        """failure_status=Noneでも接続断を再試行し、元statusのまま通知する."""
        expected_fields = ("error_message",)
        for error_code in MYSQL_RETRY_ERROR_CODES:
            with self.subTest(error_code=error_code):
                result, saved_queue, notified_states, fields = (
                    self._run_failure_retry_case(
                        error_code,
                        failure_status=None,
                    )
                )
                self.assertEqual(result["status"], "failed")
                self.assertEqual(saved_queue.status, "ready")
                self.assertIn("service unavailable", saved_queue.error_message)
                self.assertEqual(
                    notified_states,
                    [("ready", saved_queue.error_message)],
                )
                self.assertEqual(
                    fields,
                    [expected_fields, expected_fields],
                )
        self.assertEqual(mock_close_all.call_count, 2)

    @patch("twitter.db.connections.close_all")
    def test_non_connection_operational_error_propagates_without_persisting(
        self,
        mock_close_all,
    ):
        """非接続系OperationalErrorは再試行・保存・通知せず伝播する."""
        self._reset_queue_state(status="ready", error_message="before save")
        notifier = MagicMock()
        update_fields_history = []

        def fail_save(*_args, **kwargs):
            update_fields_history.append(tuple(kwargs.get("update_fields") or ()))
            raise OperationalError(
                MYSQL_NON_RETRY_ERROR_CODE,
                "Column cannot be null",
            )

        with patch.object(self.queue_item, "save", side_effect=fail_save):
            with self.assertRaises(OperationalError):
                post_tweet_queue_item(
                    self.queue_item,
                    post_tweet_func=lambda *_args, **_kwargs: {
                        "ok": False,
                        "data": None,
                        "status_code": 500,
                        "error_body": "failure",
                    },
                    notify_failure_func=notifier,
                )

        saved_queue = TweetQueue.objects.get(pk=self.queue_item.pk)
        self.assertEqual(saved_queue.status, "ready")
        self.assertEqual(saved_queue.error_message, "before save")
        self.assertEqual(update_fields_history, [("error_message", "status")])
        notifier.assert_not_called()
        mock_close_all.assert_not_called()
