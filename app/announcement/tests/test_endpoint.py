"""Cloud Scheduler から呼ぶ送信エンドポイントのトークン認証と応答のテスト。"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from announcement.models import DiscordScheduledMessage

from ._helpers import FAKE_DISCORD_MESSAGE_ID, FAKE_WEBHOOK_URL, discord_response, make_message

POST_PATH = 'announcement.discord_client.requests.post'
REQUEST_TOKEN = 'test-request-token'


@override_settings(REQUEST_TOKEN=REQUEST_TOKEN, DISCORD_ANNOUNCE_WEBHOOK_URL=FAKE_WEBHOOK_URL)
class SendScheduledEndpointTests(TestCase):
    def setUp(self):
        self.url = reverse('announcement:discord_send_scheduled')
        self.message = make_message(scheduled_at=timezone.now() - timedelta(minutes=1))

    @patch(POST_PATH)
    def test_missing_token_is_rejected(self, mock_post):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 401)
        mock_post.assert_not_called()

    @patch(POST_PATH)
    def test_wrong_token_is_rejected(self, mock_post):
        response = self.client.get(self.url, HTTP_REQUEST_TOKEN='wrong-token')

        self.assertEqual(response.status_code, 401)
        mock_post.assert_not_called()

    @override_settings(REQUEST_TOKEN='')
    @patch(POST_PATH)
    def test_unset_server_token_rejects_even_empty_header(self, mock_post):
        response = self.client.get(self.url, HTTP_REQUEST_TOKEN='')

        self.assertEqual(response.status_code, 401)
        mock_post.assert_not_called()

    @patch(POST_PATH)
    def test_valid_token_sends_due_messages_and_reports_counts(self, mock_post):
        mock_post.return_value = discord_response()

        response = self.client.get(self.url, HTTP_REQUEST_TOKEN=REQUEST_TOKEN)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'sent': 1,
            'retrying': 0,
            'failed': 0,
            'skipped': 0,
            'results': [
                {'id': self.message.pk, 'outcome': 'sent', 'discord_message_id': FAKE_DISCORD_MESSAGE_ID},
            ],
        })
        self.message.refresh_from_db()
        self.assertEqual(self.message.status, DiscordScheduledMessage.Status.SENT)
        self.assertIn('no-cache', response['Cache-Control'])

    @patch(POST_PATH)
    def test_post_is_not_allowed(self, mock_post):
        response = self.client.post(self.url, HTTP_REQUEST_TOKEN=REQUEST_TOKEN)

        self.assertEqual(response.status_code, 405)
        mock_post.assert_not_called()
