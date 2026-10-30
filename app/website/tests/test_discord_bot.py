"""Discord Bot REST の送信境界。全 requests.post をモックする。"""

import traceback
from unittest.mock import Mock, patch

import requests
from django.conf import settings
from django.test import SimpleTestCase, override_settings

from website.discord_delivery import post_discord_bot, post_discord_dm
from website.discord_webhook import DISCORD_TIMEOUT_SECONDS


@override_settings(DISCORD_BOT_TOKEN='synthetic-bot-secret')
class DiscordBotPostTest(SimpleTestCase):
    def setUp(self):
        self.channel_id = '7' * 18
        self.post = self.enterContext(patch('website.discord_delivery.requests.post'))
        self.enterContext(patch.object(post_discord_bot.retry, 'sleep', lambda _: None))

    def test_posts_only_message_fields_with_auth_timeout_and_no_redirects(self):
        payload = {'content': '本文', 'embeds': [{'title': '発表'}], 'allowed_mentions': {'parse': []}}
        webhook_payload = {**payload, 'username': 'Webhook', 'avatar_url': 'https://example.com/avatar', 'thread_name': '発表'}
        for status in (200, 201, 204, 299):
            with self.subTest(status=status):
                self.post.reset_mock()
                self.post.return_value = Mock(status_code=status)
                self.assertIs(post_discord_bot(self.channel_id, webhook_payload), self.post.return_value)
                self.post.assert_called_once_with(
                    f'https://discord.com/api/v10/channels/{self.channel_id}/messages',
                    headers={'Authorization': f'Bot {settings.DISCORD_BOT_TOKEN}'}, json=payload,
                    timeout=DISCORD_TIMEOUT_SECONDS, allow_redirects=False,
                )
        self.assertIn('username', webhook_payload)

    def test_all_valid_channel_lengths(self):
        self.post.return_value = Mock(status_code=200)
        for length in range(17, 21):
            post_discord_bot('7' * length, {})
        self.assertEqual(self.post.call_count, 4)

    def test_invalid_channel_or_missing_token_fails_before_http_without_exposing_values(self):
        for channel_id in ('7' * 16, '7' * 21, '７' * 18, self.channel_id + '\n', '../messages', None):
            with self.assertRaises(ValueError) as caught:
                post_discord_bot(channel_id, {})
            if channel_id:
                self.assertNotIn(channel_id, str(caught.exception))
        with override_settings(DISCORD_BOT_TOKEN=''), self.assertRaises(ValueError):
            post_discord_bot(self.channel_id, {})
        self.post.assert_not_called()

    def test_403_and_404_are_http_errors_without_retries(self):
        for status in (403, 404):
            self.post.reset_mock()
            self.post.return_value = Mock(status_code=status)
            with self.assertRaises(requests.HTTPError) as caught:
                post_discord_bot(self.channel_id, {})
            self.assertEqual(caught.exception.response.status_code, status)
            self.post.assert_called_once()
            self.assertNotIn(self.channel_id, str(caught.exception))

    def test_network_errors_are_retried_with_safe_exception_text_and_standard_waits(self):
        waits = []
        post_discord_bot.retry.sleep = waits.append
        for error_type in (requests.Timeout, requests.ConnectionError):
            self.post.reset_mock()
            waits.clear()
            self.post.side_effect = error_type(f'{settings.DISCORD_BOT_TOKEN} {self.channel_id}')
            with self.assertRaises(requests.RequestException) as caught:
                post_discord_bot(self.channel_id, {})
            self.assertEqual(self.post.call_count, 3)
            self.assertEqual(waits, [1.0, 2.0])
            self.assertNotIn(self.channel_id, str(caught.exception))
            self.assertNotIn(settings.DISCORD_BOT_TOKEN, str(caught.exception))


@override_settings(DISCORD_BOT_TOKEN='synthetic-bot-secret')
class DiscordDMPostTest(SimpleTestCase):
    def setUp(self):
        self.uid = '8' * 18
        self.channel_id = '7' * 18
        self.post = self.enterContext(patch('website.discord_delivery.requests.post'))
        self.channel_response = Mock(status_code=200)
        self.channel_response.json.return_value = {'id': self.channel_id}
        self.message_response = Mock(status_code=200)
        self.post.side_effect = [self.channel_response, self.message_response]

    def test_opens_dm_then_posts_message_with_mentions_disabled(self):
        payload = {'content': '発表資料', 'allowed_mentions': {'parse': ['everyone']}, 'username': 'ignored'}
        self.assertIs(post_discord_dm(self.uid, payload), self.message_response)
        self.assertEqual(self.post.call_count, 2)
        first, second = self.post.call_args_list
        self.assertEqual(first.args, ('https://discord.com/api/v10/users/@me/channels',))
        self.assertEqual(first.kwargs['json'], {'recipient_id': self.uid})
        self.assertEqual(second.args, (f'https://discord.com/api/v10/channels/{self.channel_id}/messages',))
        self.assertEqual(second.kwargs['json'], {'content': '発表資料', 'allowed_mentions': {'parse': []}})
        for call in (first, second):
            self.assertEqual(call.kwargs['headers'], {'Authorization': f'Bot {settings.DISCORD_BOT_TOKEN}'})
            self.assertEqual(call.kwargs['timeout'], DISCORD_TIMEOUT_SECONDS)
            self.assertIs(call.kwargs['allow_redirects'], False)
        self.assertEqual(payload['allowed_mentions'], {'parse': ['everyone']})

    def test_valid_recipient_lengths(self):
        for length in range(17, 21):
            self.post.side_effect = [self.channel_response, self.message_response]
            post_discord_dm('8' * length, {'content': '資料'})

    def test_invalid_recipient_or_missing_token_never_posts(self):
        for uid in ('8' * 16, '8' * 21, '８' * 18, self.uid + '\n', '../channels', None):
            with self.subTest(uid=uid), self.assertRaises(ValueError) as caught:
                post_discord_dm(uid, {})
            if uid:
                self.assertNotIn(uid, str(caught.exception))
        with override_settings(DISCORD_BOT_TOKEN=''), self.assertRaises(ValueError):
            post_discord_dm(self.uid, {})
        self.post.assert_not_called()

    def test_http_errors_in_either_step_are_not_retried_and_are_safe(self):
        for step in (1, 2):
            for status in (302, 403, 429, 500):
                with self.subTest(step=step, status=status):
                    self.post.reset_mock()
                    failure = Mock(status_code=status)
                    self.post.side_effect = [failure] if step == 1 else [self.channel_response, failure]
                    with self.assertRaises(requests.HTTPError) as caught:
                        post_discord_dm(self.uid, {})
                    self.assertEqual(caught.exception.response.status_code, status)
                    self.assertEqual(self.post.call_count, step)
                    self.assert_safe_exception(caught.exception)

    def test_network_errors_in_either_step_are_not_retried_and_are_safe(self):
        for step in (1, 2):
            for error_type in (requests.Timeout, requests.ConnectionError):
                with self.subTest(step=step, error=error_type):
                    self.post.reset_mock()
                    failure = error_type(f'{settings.DISCORD_BOT_TOKEN} {self.uid} https://discord.com/private')
                    self.post.side_effect = [failure] if step == 1 else [self.channel_response, failure]
                    with self.assertRaises(requests.RequestException) as caught:
                        post_discord_dm(self.uid, {})
                    self.assertEqual(self.post.call_count, step)
                    self.assert_safe_exception(caught.exception)

    def test_invalid_channel_response_never_posts_message(self):
        for body in (None, [], {}, {'id': '../messages'}, {'id': '7' * 16}, {'id': self.channel_id + '\n'}):
            with self.subTest(body=body):
                self.post.reset_mock()
                self.channel_response.json.return_value = body
                self.post.side_effect = [self.channel_response]
                with self.assertRaises(ValueError) as caught:
                    post_discord_dm(self.uid, {})
                self.post.assert_called_once()
                self.assert_safe_exception(caught.exception)
        self.post.reset_mock()
        self.channel_response.json.side_effect = ValueError(f'{settings.DISCORD_BOT_TOKEN} {self.uid}')
        self.post.side_effect = [self.channel_response]
        with self.assertRaises(ValueError) as caught:
            post_discord_dm(self.uid, {})
        self.post.assert_called_once()
        self.assert_safe_exception(caught.exception)

    def assert_safe_exception(self, exception):
        rendered = ''.join(traceback.format_exception(exception))
        for value in (settings.DISCORD_BOT_TOKEN, self.uid, self.channel_id, 'https://discord.com'):
            self.assertNotIn(value, rendered)
