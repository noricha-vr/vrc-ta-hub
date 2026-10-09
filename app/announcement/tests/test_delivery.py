"""予約の送信処理（二重送信の防止・再試行・メンションの制御・URL を出さないこと）のテスト。"""
from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

import requests
from django.db import DatabaseError, connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from urllib3.exceptions import MaxRetryError, NewConnectionError

from announcement import delivery
from announcement.delivery import (
    MAX_MESSAGES_PER_RUN,
    MAX_SEND_ATTEMPTS,
    process_due_messages,
)
from announcement.discord_client import SendResult
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
        self.assertEqual(result['skipped'], 1)
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
    def test_lost_claim_is_not_retaken_in_the_same_run(self, mock_post):
        """リースを付けられなかった行は送らず、その回では候補から外して取り直さない。"""
        message = make_message(scheduled_at=NOW - timedelta(minutes=1))
        due_calls = []

        def due_but_never_claimable(now):
            due_calls.append(now)
            if len(due_calls) % 2 == 1:
                # 選ぶ時は候補に出るが、リースを付ける時には別の実行に先を越されている
                return DiscordScheduledMessage.objects.filter(pk=message.pk)
            return DiscordScheduledMessage.objects.none()

        with patch.object(DiscordScheduledMessage.objects, 'due', side_effect=due_but_never_claimable):
            result = process_due_messages(now=NOW)

        mock_post.assert_not_called()
        # 選ぶ → 取り損ねる → 外して選び直す（候補なし）の 3 回だけ
        self.assertEqual(len(due_calls), 3)
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(result['results'], [{'id': message.pk, 'outcome': 'skipped', 'reason': 'claimed_by_another_run'}])
        message.refresh_from_db()
        self.assertEqual(message.attempt_count, 0)

    @patch(POST_PATH)
    def test_lost_claim_moves_on_to_the_next_message(self, mock_post):
        """先を越された行があっても、その回を止めずに次の予約を送る。"""
        mock_post.return_value = discord_response()
        taken = make_message(body='先を越される予約', scheduled_at=NOW - timedelta(minutes=2))
        other = make_message(body='次の予約', scheduled_at=NOW - timedelta(minutes=1))
        original_due = DiscordScheduledMessage.objects.due
        due_calls = []

        def due_taken_by_another_run_once(now):
            due_calls.append(now)
            if len(due_calls) == 2:
                # 1 件目にリースを付ける直前に、別の実行がその行を取った
                DiscordScheduledMessage.objects.filter(pk=taken.pk).update(
                    lease_token='another-run', lease_expires_at=NOW + timedelta(minutes=5),
                )
            return original_due(now)

        with patch.object(DiscordScheduledMessage.objects, 'due', side_effect=due_taken_by_another_run_once):
            result = process_due_messages(now=NOW)

        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args.kwargs['json']['content'], '次の予約')
        other.refresh_from_db()
        taken.refresh_from_db()
        self.assertEqual(other.status, Status.SENT)
        self.assertEqual((taken.status, taken.lease_token), (Status.SCHEDULED, 'another-run'))
        self.assertEqual((result['sent'], result['skipped']), (1, 1))

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
class ExpiredLeaseTests(TestCase):
    @patch(POST_PATH)
    def test_expired_leases_are_failed_in_one_update_and_logged_together(self, mock_post):
        expired = [
            make_message(
                scheduled_at=NOW - timedelta(minutes=10),
                lease_token=f'crashed-run-{index}',
                lease_expires_at=NOW - timedelta(seconds=1),
                attempt_count=1,
            )
            for index in range(2)
        ]
        still_running = make_message(
            scheduled_at=NOW - timedelta(minutes=1), lease_token='running', lease_expires_at=NOW + timedelta(minutes=4),
        )

        with CaptureQueriesContext(connection) as queries, \
                self.assertLogs('announcement.delivery', level='WARNING') as logs:
            result = process_due_messages(now=NOW)

        updates = [
            query['sql'] for query in queries.captured_queries
            if query['sql'].lstrip().upper().startswith('UPDATE') and 'discord_scheduled_message' in query['sql']
        ]
        self.assertEqual(len(updates), 1)
        expired_ids = sorted(message.pk for message in expired)
        self.assertEqual(sorted(DiscordScheduledMessage.objects.filter(status=Status.FAILED).values_list('pk', flat=True)), expired_ids)
        still_running.refresh_from_db()
        self.assertEqual(still_running.status, Status.SCHEDULED)
        record = next(record for record in logs.records if record.getMessage().startswith('Discord scheduled message leases expired'))
        self.assertEqual(record.expired_lease_count, 2)
        self.assertEqual(sorted(record.expired_lease_ids), expired_ids)
        self.assertEqual(result['failed'], 2)
        mock_post.assert_not_called()


@override_settings(DISCORD_ANNOUNCE_WEBHOOK_URL=FAKE_WEBHOOK_URL)
class RetryAndFailureTests(TestCase):
    @patch(POST_PATH)
    def test_server_error_is_not_retried_because_it_may_have_arrived(self, mock_post):
        """5xx は Discord 側で作られたかどうか分からないので、自動では送り直さない。"""
        for status_code in (500, 502, 503, 504):
            with self.subTest(status_code=status_code):
                mock_post.reset_mock()
                mock_post.return_value = discord_response(status_code, {'message': 'Server Error'})
                message = make_message(scheduled_at=NOW - timedelta(minutes=1))

                result = process_due_messages(now=NOW)
                process_due_messages(now=NOW + timedelta(hours=1))

                mock_post.assert_called_once()
                message.refresh_from_db()
                self.assertEqual(result['failed'], 1)
                self.assertEqual(message.status, Status.FAILED)
                self.assertIn(f'HTTP {status_code}', message.last_error)
                self.assertIn('チャンネルを確認してから再送', message.last_error)

    @patch(POST_PATH)
    def test_rate_limit_is_retried_later_and_fails_after_limit(self, mock_post):
        mock_post.return_value = discord_response(429, {'message': 'You are being rate limited.'})
        message = make_message(scheduled_at=NOW - timedelta(minutes=1))

        first = process_due_messages(now=NOW)

        message.refresh_from_db()
        self.assertEqual(first['retrying'], 1)
        self.assertEqual(message.status, Status.SCHEDULED)
        self.assertEqual(message.attempt_count, 1)
        self.assertEqual(message.next_attempt_at, NOW + delivery.RETRY_DELAYS[0])
        self.assertIn('HTTP 429', message.last_error)

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
    def test_tls_handshake_failure_is_retried(self, mock_post):
        mock_post.side_effect = requests.exceptions.SSLError('handshake failure')
        message = make_message(scheduled_at=NOW - timedelta(minutes=1))

        result = process_due_messages(now=NOW)

        message.refresh_from_db()
        self.assertEqual(result['retrying'], 1)
        self.assertEqual(message.status, Status.SCHEDULED)
        self.assertEqual(message.next_attempt_at, NOW + delivery.RETRY_DELAYS[0])
        self.assertIn('接続できませんでした（SSLError）', message.last_error)

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

    @patch(POST_PATH)
    def test_only_discord_com_webhook_is_called(self, mock_post):
        """集会の通知先と同じ検証で、discord.com の webhook だけに送る。"""
        rejected = [
            'https://example.com/api/webhooks/1/x',
            'https://discordapp.com/api/webhooks/1/x',
            'https://ptb.discord.com/api/webhooks/1/x',
            'https://canary.discord.com/api/webhooks/1/x',
        ]
        for webhook_url in rejected:
            with self.subTest(webhook_url=webhook_url), override_settings(DISCORD_ANNOUNCE_WEBHOOK_URL=webhook_url):
                message = make_message(scheduled_at=NOW - timedelta(minutes=1))

                process_due_messages(now=NOW)

                mock_post.assert_not_called()
                message.refresh_from_db()
                self.assertEqual(message.status, Status.FAILED)
                self.assertIn('Discord の webhook の形式ではありません', message.last_error)


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


@override_settings(DISCORD_ANNOUNCE_WEBHOOK_URL=FAKE_WEBHOOK_URL)
class UnexpectedErrorTests(TestCase):
    """1 件の予期しない例外で、残りの予約とまとめのログを止めない。"""

    def setUp(self):
        self.first = make_message(body='先の予約', scheduled_at=NOW - timedelta(minutes=2))
        self.second = make_message(body='後の予約', scheduled_at=NOW - timedelta(minutes=1))

    def test_error_while_sending_fails_that_message_and_continues(self):
        sent = SendResult(ok=True, message_id=FAKE_DISCORD_MESSAGE_ID)
        side_effect = [RuntimeError(f'boom {FAKE_WEBHOOK_URL}'), sent]

        with patch('announcement.delivery.send_announcement', side_effect=side_effect), \
                self.assertLogs('announcement', level='INFO') as logs:
            result = process_due_messages(now=NOW)

        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual(self.first.status, Status.FAILED)
        self.assertIn('送信中に予期しないエラーが起きました（RuntimeError）', self.first.last_error)
        self.assertIn('チャンネルを確認してから再送', self.first.last_error)
        self.assertEqual(self.first.lease_token, '')
        self.assertEqual(self.second.status, Status.SENT)
        self.assertEqual((result['failed'], result['sent']), (1, 1))
        self.assertTrue(any('Discord scheduled messages processed' in line for line in logs.output))
        for text in (self.first.last_error, json.dumps(result, ensure_ascii=False), '\n'.join(logs.output)):
            self.assertNotIn(FAKE_WEBHOOK_TOKEN, text)

    @patch(POST_PATH)
    def test_error_before_sending_returns_message_to_retry(self, mock_post):
        mock_post.return_value = discord_response()
        original_get = DiscordScheduledMessage.objects.get
        get_calls = []

        def get_failing_once(*args, **kwargs):
            get_calls.append(kwargs)
            if len(get_calls) == 1:
                raise DatabaseError('connection lost')
            return original_get(*args, **kwargs)

        with patch.object(DiscordScheduledMessage.objects, 'get', side_effect=get_failing_once):
            result = process_due_messages(now=NOW)

        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual(self.first.status, Status.SCHEDULED)
        self.assertEqual(self.first.lease_token, '')
        self.assertEqual(self.first.next_attempt_at, NOW + delivery.RETRY_DELAYS[0])
        self.assertIn('送る前に予期しないエラーが起きました（DatabaseError）', self.first.last_error)
        self.assertEqual(self.second.status, Status.SENT)
        self.assertEqual((result['retrying'], result['sent']), (1, 1))
        mock_post.assert_called_once()

    @patch(POST_PATH)
    def test_error_before_sending_fails_when_no_attempts_are_left(self, mock_post):
        DiscordScheduledMessage.objects.filter(pk=self.first.pk).update(attempt_count=MAX_SEND_ATTEMPTS - 1)
        DiscordScheduledMessage.objects.filter(pk=self.second.pk).update(status=Status.CANCELED)

        with patch.object(DiscordScheduledMessage.objects, 'get', side_effect=DatabaseError('connection lost')):
            result = process_due_messages(now=NOW)

        self.first.refresh_from_db()
        self.assertEqual(self.first.status, Status.FAILED)
        self.assertEqual(result['failed'], 1)
        mock_post.assert_not_called()


def _steal_lease(message_id):
    """リースの期限切れの処理が先に走り、予約を失敗にしてリースを外した状態にする。"""
    DiscordScheduledMessage.objects.filter(pk=message_id).update(
        status=Status.FAILED, last_error=delivery.ABANDONED_LEASE_ERROR, lease_token='', lease_expires_at=None,
    )


@override_settings(DISCORD_ANNOUNCE_WEBHOOK_URL=FAKE_WEBHOOK_URL)
class RecordingTests(TestCase):
    """送信の結果を記録する時の扱い（リースを失っていた時・記録で例外が出た時）。"""

    def setUp(self):
        self.message = make_message(scheduled_at=NOW - timedelta(minutes=2))

    @patch(POST_PATH)
    def test_sent_after_lease_was_lost_is_reported_without_rewriting_the_row(self, mock_post):
        def post_while_lease_expires(*args, **kwargs):
            _steal_lease(self.message.pk)
            return discord_response()

        mock_post.side_effect = post_while_lease_expires

        with self.assertLogs('announcement.delivery', level='ERROR') as logs:
            result = process_due_messages(now=NOW)

        self.message.refresh_from_db()
        self.assertEqual(self.message.status, Status.FAILED)
        self.assertEqual(self.message.discord_message_id, '')
        self.assertEqual(result['results'], [{
            'id': self.message.pk, 'outcome': 'sent_unrecorded',
            'discord_message_id': FAKE_DISCORD_MESSAGE_ID, 'reason': 'lease_lost',
        }])
        self.assertEqual((result['sent'], result['sent_unrecorded'], result['failed']), (0, 1, 0))
        record = next(record for record in logs.records if 'could not be recorded' in record.getMessage())
        self.assertEqual(record.scheduled_message_id, self.message.pk)
        self.assertEqual(record.discord_message_id, FAKE_DISCORD_MESSAGE_ID)
        self.assertNotIn(FAKE_WEBHOOK_TOKEN, '\n'.join(logs.output))

    @patch(POST_PATH)
    def test_record_error_after_sent_is_reported_as_sent_unrecorded(self, mock_post):
        mock_post.return_value = discord_response()
        later = make_message(body='後の予約', scheduled_at=NOW - timedelta(minutes=1))
        original_record_sent = delivery._record_sent
        record_calls = []

        def record_sent_failing_once(*args, **kwargs):
            record_calls.append(args)
            if len(record_calls) == 1:
                raise DatabaseError('connection lost')
            return original_record_sent(*args, **kwargs)

        with patch('announcement.delivery._record_sent', side_effect=record_sent_failing_once), \
                self.assertLogs('announcement.delivery', level='ERROR') as logs:
            result = process_due_messages(now=NOW)

        first = result['results'][0]
        self.assertEqual((first['id'], first['outcome']), (self.message.pk, 'sent_unrecorded'))
        self.assertEqual(first['discord_message_id'], FAKE_DISCORD_MESSAGE_ID)
        self.assertEqual((first['reason'], first['error_type']), ('record_failed', 'DatabaseError'))
        record = next(record for record in logs.records if 'could not be recorded' in record.getMessage())
        self.assertEqual(record.discord_message_id, FAKE_DISCORD_MESSAGE_ID)
        # 送れた予約の行は書き換えない（リースの期限切れの処理に任せる）。残りの予約は送る
        self.message.refresh_from_db()
        later.refresh_from_db()
        self.assertEqual(self.message.status, Status.SCHEDULED)
        self.assertNotEqual(self.message.lease_token, '')
        self.assertEqual(later.status, Status.SENT)
        self.assertEqual(mock_post.call_count, 2)

    @patch(POST_PATH)
    def test_record_error_after_rate_limit_does_not_say_it_may_have_arrived(self, mock_post):
        mock_post.return_value = discord_response(429, {'message': 'You are being rate limited.'})

        with patch('announcement.delivery._release_for_retry', side_effect=DatabaseError('connection lost')):
            result = process_due_messages(now=NOW)

        self.message.refresh_from_db()
        self.assertEqual(self.message.status, Status.FAILED)
        self.assertIn('HTTP 429', self.message.last_error)
        self.assertIn('Discord には届いていない', self.message.last_error)
        self.assertNotIn('届いている可能性', self.message.last_error)
        self.assertEqual(result['results'][0]['outcome'], 'failed')
        self.assertEqual(result['results'][0]['stage'], 'recording')

    @patch(POST_PATH)
    def test_retry_is_not_counted_when_the_lease_was_lost(self, mock_post):
        def rate_limited_while_lease_expires(*args, **kwargs):
            _steal_lease(self.message.pk)
            return discord_response(429, {'message': 'You are being rate limited.'})

        mock_post.side_effect = rate_limited_while_lease_expires

        result = process_due_messages(now=NOW)

        self.assertEqual(result['retrying'], 0)
        self.assertEqual(result['results'][0]['outcome'], 'failed')
        self.assertEqual(result['results'][0]['reason'], 'lease_lost')
        self.message.refresh_from_db()
        self.assertEqual(self.message.last_error, delivery.ABANDONED_LEASE_ERROR)
        self.assertIsNone(self.message.next_attempt_at)
