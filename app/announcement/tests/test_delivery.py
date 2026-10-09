"""予約の送信処理（二重送信の防止・再試行・メンションの制御・URL を出さないこと）のテスト。"""
from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

import requests
from django.test import TestCase, override_settings
from urllib3.exceptions import MaxRetryError, NewConnectionError

from announcement import delivery
from announcement.delivery import (
    LEASE_DURATION,
    MAX_MESSAGES_PER_RUN,
    MAX_SEND_ATTEMPTS,
    process_due_messages,
)
from announcement.models import DiscordScheduledMessage

from ._helpers import (
    FAKE_DISCORD_MESSAGE_ID,
    FAKE_WEBHOOK_TOKEN,
    FAKE_WEBHOOK_URL,
    discord_response,
    jst,
    make_message,
)

Status = DiscordScheduledMessage.Status
POST_PATH = 'announcement.discord_client.requests.post'
NOW = jst(2026, 10, 1, 20, 0)


@override_settings(DISCORD_ANNOUNCE_WEBHOOK_URL=FAKE_WEBHOOK_URL)
class SendDueMessageTests(TestCase):
    @patch(POST_PATH)
    def test_due_message_is_sent_once_and_recorded(self, mock_post):
        mock_post.return_value = discord_response()
        message = make_message(scheduled_at=NOW - timedelta(minutes=1))

        result = process_due_messages(now=NOW)

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], FAKE_WEBHOOK_URL)
        self.assertEqual(kwargs['params'], {'wait': 'true'})
        self.assertEqual(kwargs['json']['content'], 'テストの告知です')
        message.refresh_from_db()
        self.assertEqual(message.status, Status.SENT)
        self.assertEqual(message.discord_message_id, FAKE_DISCORD_MESSAGE_ID)
        self.assertIsNotNone(message.sent_at)
        self.assertEqual(message.attempt_count, 1)
        self.assertEqual(message.lease_token, '')
        self.assertIsNone(message.lease_expires_at)
        self.assertEqual(result['sent'], 1)
        self.assertEqual(result['results'], [
            {'id': message.pk, 'outcome': 'sent', 'discord_message_id': FAKE_DISCORD_MESSAGE_ID},
        ])

    @patch(POST_PATH)
    def test_future_canceled_and_failed_messages_are_not_sent(self, mock_post):
        make_message(scheduled_at=NOW + timedelta(minutes=1))
        make_message(scheduled_at=NOW - timedelta(minutes=1), status=Status.CANCELED)
        make_message(scheduled_at=NOW - timedelta(minutes=1), status=Status.FAILED)

        result = process_due_messages(now=NOW)

        mock_post.assert_not_called()
        self.assertEqual(result['results'], [])

    @patch(POST_PATH)
    def test_older_messages_are_sent_first_and_run_is_capped(self, mock_post):
        mock_post.return_value = discord_response()
        messages = [
            make_message(body=f'告知 {index}', scheduled_at=NOW - timedelta(minutes=index))
            for index in range(MAX_MESSAGES_PER_RUN + 1)
        ]

        result = process_due_messages(now=NOW)

        self.assertEqual(result['sent'], MAX_MESSAGES_PER_RUN)
        sent_bodies = [call.kwargs['json']['content'] for call in mock_post.call_args_list]
        self.assertEqual(sent_bodies, [f'告知 {index}' for index in range(MAX_MESSAGES_PER_RUN, 0, -1)])
        messages[0].refresh_from_db()
        self.assertEqual(messages[0].status, Status.SCHEDULED)


@override_settings(DISCORD_ANNOUNCE_WEBHOOK_URL=FAKE_WEBHOOK_URL)
class DoubleSendPreventionTests(TestCase):
    @patch(POST_PATH)
    def test_running_twice_sends_only_once(self, mock_post):
        mock_post.return_value = discord_response()
        make_message(scheduled_at=NOW - timedelta(minutes=1))

        process_due_messages(now=NOW)
        second = process_due_messages(now=NOW + timedelta(minutes=1))

        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(second['results'], [])

    @patch(POST_PATH)
    def test_message_leased_by_another_run_is_not_sent(self, mock_post):
        message = make_message(
            scheduled_at=NOW - timedelta(minutes=1),
            lease_token='another-run',
            lease_expires_at=NOW + timedelta(minutes=4),
            attempt_count=1,
        )

        process_due_messages(now=NOW)

        mock_post.assert_not_called()
        message.refresh_from_db()
        self.assertEqual(message.status, Status.SCHEDULED)
        self.assertEqual(message.lease_token, 'another-run')

    @patch(POST_PATH)
    def test_run_started_while_sending_does_not_send_the_same_message(self, mock_post):
        """送信の応答を待っている間に次の実行が始まっても、同じ予約は送らない。"""
        message = make_message(scheduled_at=NOW - timedelta(minutes=1))
        overlapping_results = []

        def post_while_another_run_starts(*args, **kwargs):
            overlapping_results.append(process_due_messages(now=NOW + timedelta(seconds=30)))
            return discord_response()

        mock_post.side_effect = post_while_another_run_starts

        process_due_messages(now=NOW)

        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(overlapping_results[0]['results'], [])
        message.refresh_from_db()
        self.assertEqual(message.status, Status.SENT)

    @patch(POST_PATH)
    def test_message_lost_to_another_run_while_claiming_is_skipped(self, mock_post):
        """候補に選んだ直後に別の実行がリースを付けたら、送らずにスキップとして数える。"""
        message = make_message(scheduled_at=NOW - timedelta(minutes=1))
        original_due = DiscordScheduledMessage.objects.due
        claimed_by_other = []

        def due_then_taken(now):
            queryset = original_due(now)
            if not claimed_by_other:
                claimed_by_other.append(True)
                candidates = list(queryset.values_list('pk', flat=True))
                DiscordScheduledMessage.objects.filter(pk=message.pk).update(
                    lease_token='another-run', lease_expires_at=NOW + LEASE_DURATION,
                )
                return DiscordScheduledMessage.objects.filter(pk__in=candidates)
            return queryset

        with patch.object(DiscordScheduledMessage.objects, 'due', side_effect=due_then_taken):
            result = process_due_messages(now=NOW)

        mock_post.assert_not_called()
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(result['results'][0]['outcome'], 'skipped')

    @patch(POST_PATH)
    def test_expired_lease_is_failed_without_resending(self, mock_post):
        """送信処理が途中で止まった予約は、届いたか分からないので送り直さずに失敗にする。"""
        message = make_message(
            scheduled_at=NOW - timedelta(minutes=10),
            lease_token='crashed-run',
            lease_expires_at=NOW - timedelta(seconds=1),
            attempt_count=1,
        )

        result = process_due_messages(now=NOW)

        mock_post.assert_not_called()
        message.refresh_from_db()
        self.assertEqual(message.status, Status.FAILED)
        self.assertIn('チャンネルを確認してから再送', message.last_error)
        self.assertEqual(message.lease_token, '')
        self.assertEqual(result['failed'], 1)
        self.assertEqual(result['results'][0]['reason'], 'lease_expired')


@override_settings(DISCORD_ANNOUNCE_WEBHOOK_URL=FAKE_WEBHOOK_URL)
class RetryAndFailureTests(TestCase):
    @patch(POST_PATH)
    def test_server_error_is_retried_later_and_fails_after_limit(self, mock_post):
        mock_post.return_value = discord_response(503, {'message': 'Service Unavailable'})
        message = make_message(scheduled_at=NOW - timedelta(minutes=1))

        first = process_due_messages(now=NOW)

        message.refresh_from_db()
        self.assertEqual(first['retrying'], 1)
        self.assertEqual(message.status, Status.SCHEDULED)
        self.assertEqual(message.attempt_count, 1)
        self.assertEqual(message.next_attempt_at, NOW + delivery.RETRY_DELAYS[0])
        self.assertIn('HTTP 503', message.last_error)

        # 待ち時間の間は送らない
        process_due_messages(now=NOW + timedelta(minutes=1))
        self.assertEqual(mock_post.call_count, 1)

        second_time = NOW + delivery.RETRY_DELAYS[0]
        process_due_messages(now=second_time)
        third_time = second_time + delivery.RETRY_DELAYS[1]
        last = process_due_messages(now=third_time)

        self.assertEqual(mock_post.call_count, MAX_SEND_ATTEMPTS)
        message.refresh_from_db()
        self.assertEqual(message.status, Status.FAILED)
        self.assertEqual(message.attempt_count, MAX_SEND_ATTEMPTS)
        self.assertEqual(last['failed'], 1)
        process_due_messages(now=third_time + timedelta(hours=1))
        self.assertEqual(mock_post.call_count, MAX_SEND_ATTEMPTS)

    @patch(POST_PATH)
    def test_rate_limited_is_retried_then_sent(self, mock_post):
        mock_post.side_effect = [
            discord_response(429, {'message': 'You are being rate limited.', 'retry_after': 1.0}),
            discord_response(),
        ]
        message = make_message(scheduled_at=NOW - timedelta(minutes=1))

        process_due_messages(now=NOW)
        process_due_messages(now=NOW + delivery.RETRY_DELAYS[0])

        message.refresh_from_db()
        self.assertEqual(message.status, Status.SENT)
        self.assertEqual(message.attempt_count, 2)
        self.assertEqual(message.last_error, '')

    @patch(POST_PATH)
    def test_client_error_fails_immediately_with_discord_reason(self, mock_post):
        mock_post.return_value = discord_response(404, {'message': 'Unknown Webhook', 'code': 10015})
        message = make_message(scheduled_at=NOW - timedelta(minutes=1))

        result = process_due_messages(now=NOW)

        message.refresh_from_db()
        self.assertEqual(message.status, Status.FAILED)
        self.assertEqual(message.attempt_count, 1)
        self.assertIn('HTTP 404', message.last_error)
        self.assertIn('Unknown Webhook', message.last_error)
        self.assertEqual(result['failed'], 1)

    @patch(POST_PATH)
    def test_connection_failure_before_sending_is_retried(self, mock_post):
        mock_post.side_effect = requests.ConnectionError(
            MaxRetryError(None, '/api/webhooks', reason=NewConnectionError(None, 'refused')),
        )
        message = make_message(scheduled_at=NOW - timedelta(minutes=1))

        result = process_due_messages(now=NOW)

        message.refresh_from_db()
        self.assertEqual(result['retrying'], 1)
        self.assertEqual(message.status, Status.SCHEDULED)
        self.assertIn('接続できませんでした', message.last_error)

    @patch(POST_PATH)
    def test_read_timeout_is_not_retried_because_it_may_have_arrived(self, mock_post):
        mock_post.side_effect = requests.ReadTimeout('read timed out')
        message = make_message(scheduled_at=NOW - timedelta(minutes=1))

        result = process_due_messages(now=NOW)

        message.refresh_from_db()
        self.assertEqual(result['failed'], 1)
        self.assertEqual(message.status, Status.FAILED)
        self.assertIn('届いている可能性', message.last_error)
        process_due_messages(now=NOW + timedelta(hours=1))
        self.assertEqual(mock_post.call_count, 1)

    @override_settings(DISCORD_ANNOUNCE_WEBHOOK_URL='')
    @patch(POST_PATH)
    def test_missing_webhook_is_recorded_as_failure_without_sending(self, mock_post):
        message = make_message(scheduled_at=NOW - timedelta(minutes=1))

        result = process_due_messages(now=NOW)

        mock_post.assert_not_called()
        message.refresh_from_db()
        self.assertEqual(message.status, Status.FAILED)
        self.assertIn('DISCORD_ANNOUNCE_WEBHOOK_URL', message.last_error)
        self.assertEqual(result['failed'], 1)

    @override_settings(DISCORD_ANNOUNCE_WEBHOOK_URL='https://example.com/api/webhooks/1/x')
    @patch(POST_PATH)
    def test_non_discord_webhook_is_not_called(self, mock_post):
        message = make_message(scheduled_at=NOW - timedelta(minutes=1))

        process_due_messages(now=NOW)

        mock_post.assert_not_called()
        message.refresh_from_db()
        self.assertEqual(message.status, Status.FAILED)


@override_settings(DISCORD_ANNOUNCE_WEBHOOK_URL=FAKE_WEBHOOK_URL)
class MentionControlTests(TestCase):
    def _sent_allowed_mentions(self, mock_post) -> list[str]:
        return mock_post.call_args.kwargs['json']['allowed_mentions']['parse']

    @patch(POST_PATH)
    def test_confirmed_message_allows_everyone(self, mock_post):
        mock_post.return_value = discord_response()
        make_message(
            body='@everyone 今夜の告知です',
            scheduled_at=NOW - timedelta(minutes=1),
            mention_everyone_confirmed=True,
        )

        process_due_messages(now=NOW)

        self.assertEqual(self._sent_allowed_mentions(mock_post), ['roles', 'users', 'everyone'])

    @patch(POST_PATH)
    def test_unconfirmed_message_allows_only_roles_and_users(self, mock_post):
        mock_post.return_value = discord_response()
        make_message(
            body='@everyone <@&123> <@456> 今夜の告知です',
            scheduled_at=NOW - timedelta(minutes=1),
            mention_everyone_confirmed=False,
        )

        process_due_messages(now=NOW)

        self.assertEqual(self._sent_allowed_mentions(mock_post), ['roles', 'users'])


@override_settings(DISCORD_ANNOUNCE_WEBHOOK_URL=FAKE_WEBHOOK_URL)
class WebhookSecrecyTests(TestCase):
    @patch(POST_PATH)
    def test_webhook_url_is_not_left_in_error_logs_or_result(self, mock_post):
        """requests の例外の文字列には URL が入る。DB・ログ・応答のどこにも残さない。"""
        mock_post.side_effect = requests.ConnectionError(
            f"HTTPSConnectionPool(host='discord.com', port=443): Max retries exceeded with url: "
            f'/api/webhooks/123456789/{FAKE_WEBHOOK_TOKEN}?wait=true',
        )
        message = make_message(scheduled_at=NOW - timedelta(minutes=1))

        with self.assertLogs('announcement', level='INFO') as logs:
            result = process_due_messages(now=NOW)

        message.refresh_from_db()
        self.assertEqual(message.status, Status.FAILED)
        self.assertNotIn(FAKE_WEBHOOK_TOKEN, message.last_error)
        self.assertNotIn(FAKE_WEBHOOK_TOKEN, json.dumps(result, ensure_ascii=False))
        self.assertNotIn(FAKE_WEBHOOK_TOKEN, '\n'.join(logs.output))

    @patch(POST_PATH)
    def test_url_in_discord_error_body_is_masked(self, mock_post):
        mock_post.return_value = discord_response(400, {'message': f'Invalid {FAKE_WEBHOOK_URL}', 'code': 50006})
        message = make_message(scheduled_at=NOW - timedelta(minutes=1))

        process_due_messages(now=NOW)

        message.refresh_from_db()
        self.assertNotIn(FAKE_WEBHOOK_TOKEN, message.last_error)
        self.assertIn('[URL]', message.last_error)
        self.assertIn('50006', message.last_error)


@override_settings(DISCORD_ANNOUNCE_WEBHOOK_URL=FAKE_WEBHOOK_URL)
class DeliveryLoggingTests(TestCase):
    @patch(POST_PATH)
    def test_summary_counts_are_logged_as_structured_fields(self, mock_post):
        mock_post.return_value = discord_response()
        make_message(scheduled_at=NOW - timedelta(minutes=1))

        with self.assertLogs('announcement.delivery', level='INFO') as logs:
            process_due_messages(now=NOW)

        summary = [record for record in logs.records if record.getMessage().startswith('Discord scheduled messages processed')]
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0].delivery_sent_count, 1)
        self.assertEqual(summary[0].delivery_failed_count, 0)
        self.assertEqual(summary[0].delivery_skipped_count, 0)
