"""実際のDiscordには接続せず、予約の送信境界と競合を確認する。"""

import json
from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest.mock import patch

import requests
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings

from discord_scheduler import services
from discord_scheduler.models import ScheduledDiscordPost, validate_discord_content


WEBHOOK_URL = "https://discord.com/api/webhooks/123456789/test-only-token"
CHANNEL_URL = "https://discord.com/channels/1143765879377645628/1304472925058891899"
CHANNEL_ID = "1304472925058891899"
NOW = datetime(2026, 10, 8, 11, 0, tzinfo=datetime_timezone.utc)


def response(status=200, data=None, *, headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(data if data is not None else {"id": "123", "channel_id": CHANNEL_ID}).encode()
    result.headers.update(headers or {})
    return result


class DiscordContentValidationTest(SimpleTestCase):
    def test_preserves_multiline_content_and_allows_limit(self):
        validate_discord_content("あ" * 2000)
        validate_discord_content("😀" * 1000)
        validate_discord_content("\n連絡\nhttps://example.com/\n")

    def test_rejects_blank_and_content_over_discord_limit(self):
        for content in ("", " \n\t", "あ" * 2001, "😀" * 1001, "a" * 1999 + "😀", "\ud800"):
            with self.subTest(content_length=len(content)):
                with self.assertRaises(ValidationError):
                    validate_discord_content(content)

    def test_configuration_requires_fixed_discord_urls(self):
        with override_settings(DISCORD_SCHEDULED_WEBHOOK_URL=WEBHOOK_URL, DISCORD_SCHEDULED_CHANNEL_URL=CHANNEL_URL):
            self.assertTrue(services.is_configured())
        for invalid_url in (
            "",
            "http://discord.com/api/webhooks/123/token",
            "https://example.com/api/webhooks/123/token",
            "https://discord.com.evil.test/api/webhooks/123/token",
            "https://discord.com/api/webhooks/123/token?thread_id=456",
        ):
            with self.subTest(webhook_url=invalid_url), override_settings(
                DISCORD_SCHEDULED_WEBHOOK_URL=invalid_url,
                DISCORD_SCHEDULED_CHANNEL_URL=CHANNEL_URL,
            ):
                self.assertFalse(services.is_configured())
        with override_settings(DISCORD_SCHEDULED_WEBHOOK_URL=WEBHOOK_URL, DISCORD_SCHEDULED_CHANNEL_URL=""):
            self.assertFalse(services.is_configured())


@override_settings(DISCORD_SCHEDULED_WEBHOOK_URL=WEBHOOK_URL, DISCORD_SCHEDULED_CHANNEL_URL=CHANNEL_URL)
class ScheduledDiscordPostServiceTest(TestCase):
    def setUp(self):
        clock_patch = patch("discord_scheduler.services.timezone.now", return_value=NOW)
        http_patch = patch("discord_scheduler.services.requests.post", return_value=response())
        self.clock = clock_patch.start()
        self.http_post = http_patch.start()
        self.addCleanup(clock_patch.stop)
        self.addCleanup(http_patch.stop)

    def make_post(self, **values):
        defaults = {"content": "案内本文", "scheduled_at": NOW}
        defaults.update(values)
        return ScheduledDiscordPost.objects.create(**defaults)

    def test_sends_due_posts_once_with_exact_content_and_records_message_url(self):
        content = "@everyone\n<@123> <@&456> 連絡です。\nhttps://example.com/details\n"
        post = self.make_post(content=content)

        counts = services.process_scheduled_posts()
        post.refresh_from_db()

        self.assertEqual(counts, {"processed": 1, "sent": 1, "failed": 0, "needs_review": 0, "deferred": 0})
        self.assertEqual(post.status, ScheduledDiscordPost.Status.SENT)
        self.assertEqual(post.started_at, NOW)
        self.assertEqual(post.sent_at, NOW)
        self.assertEqual(post.message_url, CHANNEL_URL + "/123")
        self.http_post.assert_called_once_with(
            WEBHOOK_URL,
            params={"wait": "true"},
            json={"content": content, "allowed_mentions": {"parse": ["users", "roles", "everyone"]}},
            timeout=(5, 20),
            allow_redirects=False,
        )
        self.assertEqual(services.process_scheduled_posts()["processed"], 0)
        self.assertEqual(self.http_post.call_count, 1)

    def test_only_due_scheduled_posts_are_sent_in_time_order_with_limit(self):
        due = self.make_post(content="期限到来", scheduled_at=NOW)
        overdue = self.make_post(content="遅れている予約", scheduled_at=NOW - timedelta(days=1))
        self.make_post(content="未来", scheduled_at=NOW + timedelta(seconds=1))
        for status in (
            ScheduledDiscordPost.Status.CANCELLED,
            ScheduledDiscordPost.Status.SENT,
            ScheduledDiscordPost.Status.FAILED,
            ScheduledDiscordPost.Status.NEEDS_REVIEW,
            ScheduledDiscordPost.Status.SENDING,
        ):
            self.make_post(status=status, started_at=NOW)

        self.assertEqual(services.process_scheduled_posts(limit=1)["sent"], 1)
        self.assertEqual(self.http_post.call_args.kwargs["json"]["content"], overdue.content)
        due.refresh_from_db()
        self.assertEqual(due.status, ScheduledDiscordPost.Status.SCHEDULED)
        self.assertEqual(services.process_scheduled_posts()["sent"], 1)
        self.assertEqual(self.http_post.call_count, 2)

    def test_overlapping_invocation_does_not_send_claimed_post_twice(self):
        post = self.make_post()

        def overlapping_request(*args, **kwargs):
            counts = services.process_scheduled_posts()
            self.assertEqual(counts["processed"], 0)
            post.refresh_from_db()
            self.assertEqual(post.status, ScheduledDiscordPost.Status.SENDING)
            return response()

        self.http_post.side_effect = overlapping_request
        self.assertEqual(services.process_scheduled_posts()["sent"], 1)
        self.http_post.assert_called_once()

    def interleave_before_claim(self, post, update):
        original_due_posts = services._due_posts
        calls = 0

        def due_posts(now):
            nonlocal calls
            calls += 1
            if calls == 2:
                ScheduledDiscordPost.objects.filter(pk=post.pk, status=ScheduledDiscordPost.Status.SCHEDULED).update(
                    **update
                )
            return original_due_posts(now)

        return patch("discord_scheduler.services._due_posts", side_effect=due_posts)

    def test_cancel_between_selection_and_claim_prevents_post(self):
        post = self.make_post()
        with self.interleave_before_claim(post, {"status": ScheduledDiscordPost.Status.CANCELLED}):
            self.assertEqual(services.process_scheduled_posts()["processed"], 0)
        self.http_post.assert_not_called()

    def test_reschedule_between_selection_and_claim_prevents_early_post(self):
        post = self.make_post()
        with self.interleave_before_claim(post, {"scheduled_at": NOW + timedelta(hours=1)}):
            self.assertEqual(services.process_scheduled_posts()["processed"], 0)
        self.http_post.assert_not_called()

    def test_content_edit_before_claim_sends_latest_saved_content(self):
        post = self.make_post(content="修正前")
        with self.interleave_before_claim(post, {"content": "修正後"}):
            self.assertEqual(services.process_scheduled_posts()["sent"], 1)
        self.assertEqual(self.http_post.call_args.kwargs["json"]["content"], "修正後")

    def test_claim_prevents_pending_only_edit_or_cancel(self):
        post = self.make_post(content="確定した本文")

        def try_update_after_claim(*args, **kwargs):
            pending = ScheduledDiscordPost.objects.filter(pk=post.pk, status=ScheduledDiscordPost.Status.SCHEDULED)
            self.assertEqual(pending.update(content="競合した修正"), 0)
            self.assertEqual(pending.update(status=ScheduledDiscordPost.Status.CANCELLED), 0)
            return response()

        self.http_post.side_effect = try_update_after_claim
        self.assertEqual(services.process_scheduled_posts()["sent"], 1)
        post.refresh_from_db()
        self.assertEqual(post.content, "確定した本文")

    def test_invalid_content_is_not_sent_even_if_form_validation_was_bypassed(self):
        for content in (" \n", "😀" * 1001):
            with self.subTest(length=len(content)):
                post = self.make_post(content=content)
                self.assertEqual(services.process_scheduled_posts()["failed"], 1)
                post.refresh_from_db()
                self.assertEqual(post.status, ScheduledDiscordPost.Status.FAILED)
        self.http_post.assert_not_called()

    @override_settings(DISCORD_SCHEDULED_WEBHOOK_URL="")
    def test_missing_configuration_fails_without_network(self):
        post = self.make_post()
        self.assertEqual(services.process_scheduled_posts()["failed"], 1)
        post.refresh_from_db()
        self.assertIn("設定", post.error_message)
        self.http_post.assert_not_called()

    def test_transport_errors_are_not_retried_and_do_not_expose_webhook_token(self):
        for error_type in (requests.Timeout, requests.ConnectionError, requests.RequestException):
            with self.subTest(error_type=error_type):
                self.http_post.reset_mock()
                self.http_post.side_effect = error_type(f"request to {WEBHOOK_URL} failed")
                post = self.make_post()

                self.assertEqual(services.process_scheduled_posts()["needs_review"], 1)
                post.refresh_from_db()
                self.assertEqual(post.status, ScheduledDiscordPost.Status.NEEDS_REVIEW)
                self.assertNotIn("test-only-token", post.error_message)
                self.assertNotIn("https://", post.error_message)
                self.assertIsNone(post.sent_at)
                self.assertEqual(services.process_scheduled_posts()["processed"], 0)
                self.http_post.assert_called_once()

    def test_rejected_post_is_failed_without_automatic_retry_or_response_body_exposure(self):
        for status in (301, 400, 401, 403, 404):
            with self.subTest(status=status):
                self.http_post.reset_mock()
                self.http_post.return_value = response(status, {"message": WEBHOOK_URL})
                post = self.make_post()
                self.assertEqual(services.process_scheduled_posts()["failed"], 1)
                post.refresh_from_db()
                self.assertEqual(post.status, ScheduledDiscordPost.Status.FAILED)
                self.assertIn(str(status), post.error_message)
                self.assertNotIn("test-only-token", post.error_message)
                self.assertEqual(services.process_scheduled_posts()["processed"], 0)
                self.http_post.assert_called_once()

    def test_unknown_success_or_server_error_requires_review_without_resending(self):
        invalid_json = response(200)
        invalid_json._content = b"not json"
        for result in (
            response(200, {}),
            response(204, {}),
            invalid_json,
            response(200, {"id": "not-a-message-id"}),
            response(200, {"id": "123", "channel_id": "999"}),
            response(500, {"message": WEBHOOK_URL}),
        ):
            with self.subTest(status=result.status_code, body=result.content):
                self.http_post.reset_mock()
                self.http_post.return_value = result
                post = self.make_post()
                self.assertEqual(services.process_scheduled_posts()["needs_review"], 1)
                post.refresh_from_db()
                self.assertEqual(post.status, ScheduledDiscordPost.Status.NEEDS_REVIEW)
                self.assertEqual(post.message_url, "")
                self.assertEqual(services.process_scheduled_posts()["processed"], 0)
                self.http_post.assert_called_once()

    def test_stalled_sending_is_flagged_without_resending(self):
        stalled = self.make_post(status=ScheduledDiscordPost.Status.SENDING, started_at=NOW - timedelta(minutes=6))
        active = self.make_post(status=ScheduledDiscordPost.Status.SENDING, started_at=NOW - timedelta(seconds=30))
        counts = services.process_scheduled_posts()
        self.assertEqual(counts["needs_review"], 1)
        self.assertEqual(counts["processed"], 0)
        stalled.refresh_from_db()
        active.refresh_from_db()
        self.assertEqual(stalled.status, ScheduledDiscordPost.Status.NEEDS_REVIEW)
        self.assertEqual(active.status, ScheduledDiscordPost.Status.SENDING)
        self.http_post.assert_not_called()

    def test_rate_limit_defers_remaining_posts_and_retries_after_retry_after(self):
        first = self.make_post(content="一件目")
        second = self.make_post(content="二件目")
        self.http_post.return_value = response(429, {"retry_after": 60.5})

        counts = services.process_scheduled_posts()
        self.assertEqual(counts["processed"], 1)
        self.assertEqual(counts["deferred"], 1)
        self.http_post.assert_called_once()
        for post in (first, second):
            post.refresh_from_db()
            self.assertEqual(post.status, ScheduledDiscordPost.Status.SCHEDULED)
            self.assertEqual(post.next_attempt_at, NOW + timedelta(seconds=60.5))
            self.assertEqual(post.scheduled_at, NOW)

        self.clock.return_value = NOW + timedelta(seconds=60)
        self.assertEqual(services.process_scheduled_posts()["processed"], 0)
        self.assertEqual(self.http_post.call_count, 1)

        self.clock.return_value = NOW + timedelta(seconds=61)
        self.http_post.return_value = response()
        self.assertEqual(services.process_scheduled_posts()["sent"], 2)
        self.assertEqual(self.http_post.call_count, 3)

    def test_rate_limit_uses_retry_after_header_if_body_has_no_valid_delay(self):
        post = self.make_post()
        self.http_post.return_value = response(429, {}, headers={"Retry-After": "15"})
        self.assertEqual(services.process_scheduled_posts()["deferred"], 1)
        post.refresh_from_db()
        self.assertEqual(post.next_attempt_at, NOW + timedelta(seconds=15))

    def test_new_reservation_cannot_bypass_existing_webhook_rate_limit(self):
        self.make_post(content="送信制限を受ける予約")
        self.http_post.return_value = response(429, {"retry_after": 120})
        self.assertEqual(services.process_scheduled_posts()["deferred"], 1)

        new_post = self.make_post(content="制限中に追加した予約")
        self.assertIsNone(new_post.next_attempt_at)
        self.clock.return_value = NOW + timedelta(seconds=30)
        self.http_post.return_value = response()
        self.assertEqual(services.process_scheduled_posts()["processed"], 0)
        self.http_post.assert_called_once()

        self.clock.return_value = NOW + timedelta(seconds=121)
        self.assertEqual(services.process_scheduled_posts()["sent"], 2)

    def test_cancelling_original_reservation_does_not_release_webhook_rate_limit(self):
        original = self.make_post()
        self.http_post.return_value = response(429, {"retry_after": 120})
        self.assertEqual(services.process_scheduled_posts()["deferred"], 1)
        ScheduledDiscordPost.objects.filter(pk=original.pk).update(status=ScheduledDiscordPost.Status.CANCELLED)

        new_post = self.make_post(content="取消後に作った予約")
        self.clock.return_value = NOW + timedelta(seconds=30)
        self.http_post.return_value = response()
        self.assertEqual(services.process_scheduled_posts()["processed"], 0)
        self.http_post.assert_called_once()

        self.clock.return_value = NOW + timedelta(seconds=121)
        self.assertEqual(services.process_scheduled_posts()["sent"], 1)
        original.refresh_from_db()
        new_post.refresh_from_db()
        self.assertEqual(original.status, ScheduledDiscordPost.Status.CANCELLED)
        self.assertEqual(new_post.status, ScheduledDiscordPost.Status.SENT)

    def test_rate_limit_without_usable_delay_fails_without_immediate_retry(self):
        self.make_post()
        self.http_post.return_value = response(429, {"retry_after": "invalid"})
        self.assertEqual(services.process_scheduled_posts()["failed"], 1)
        self.assertEqual(services.process_scheduled_posts()["processed"], 0)
        self.http_post.assert_called_once()


@override_settings(DISCORD_SCHEDULED_WEBHOOK_URL=WEBHOOK_URL, DISCORD_SCHEDULED_CHANNEL_URL=CHANNEL_URL)
class ScheduledDiscordPostTransactionTest(TransactionTestCase):
    @patch("discord_scheduler.services.requests.post")
    def test_does_not_hold_database_transaction_during_network_request(self, http_post):
        ScheduledDiscordPost.objects.create(content="送信中にDBロックを持たない", scheduled_at=NOW)

        def assert_no_transaction(*args, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            return response()

        http_post.side_effect = assert_no_transaction
        with patch("discord_scheduler.services.timezone.now", return_value=NOW):
            self.assertEqual(services.process_scheduled_posts()["sent"], 1)
