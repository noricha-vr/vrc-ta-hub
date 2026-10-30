"""Discord Bot REST の送信境界。全 requests.post をモックする。"""

from unittest.mock import Mock, patch

import requests
from django.conf import settings
from django.test import SimpleTestCase, override_settings

from website.discord_delivery import post_discord_bot
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
