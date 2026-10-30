"""集会の6種類の通知を、実通信なしで Bot / Webhook に振り分ける。"""

from unittest.mock import Mock, patch

import requests
from django.conf import settings
from django.test import TestCase, override_settings

from community.models import BotInstallation
from event import notifications
from tests.factories import make_community, make_event, make_event_detail, make_user
from website.discord_delivery import post_discord_bot, select_community_discord_delivery


@override_settings(DISCORD_BOT_TOKEN='synthetic-bot-secret', DISCORD_BOT_DELIVERY_COMMUNITY_IDS='')
class CommunityBotDeliveryTest(TestCase):
    def setUp(self):
        self.webhook_url = 'https://discord.com/api/webhooks/synthetic/webhook-secret'
        self.community = make_community(webhook_url=self.webhook_url)
        self.user = make_user()
        self.detail = make_event_detail(make_event(self.community), applicant=self.user)
        self.channel_id = '7' * 18
        self.installation = BotInstallation.objects.create(
            community=self.community, guild_id='8' * 18, status='configured',
            notification_channel_id=self.channel_id,
        )
        self.enterContext(override_settings(DISCORD_BOT_DELIVERY_COMMUNITY_IDS=f' , {self.community.pk}, '))
        self.post = self.enterContext(patch('website.discord_delivery.requests.post', return_value=Mock(status_code=200)))
        self.webhook = self.enterContext(patch('event.notifications.post_discord_webhook', return_value=Mock(status_code=204)))
        self.enterContext(patch.object(post_discord_bot.retry, 'sleep', lambda _: None))
        self.senders = (
            ('article', lambda: notifications.send_discord_notification_for_article(self.detail, 'https://example.com/edit', 'https://example.com/article'), True),
            ('video_article', lambda: notifications.notify_video_article_published(self.detail), True),
            ('application', lambda: notifications._send_discord_notification_for_new_application(self.detail, 'https://example.com/review'), None),
            ('result', lambda: notifications._send_discord_notification_for_result(self.detail), None),
            ('account_linked', lambda: notifications.notify_speaker_account_linked(self.detail, self.user), None),
            ('slides', lambda: notifications.notify_slide_material_published(self.detail), None),
        )

    def reset_posts(self):
        self.post.reset_mock()
        self.webhook.reset_mock()

    def test_all_six_send_once_by_bot_with_unchanged_payload_and_no_webhook(self):
        for name, send, expected in self.senders:
            with self.subTest(notification=name):
                self.reset_posts()
                # Webhook 経路の既存の payload と一致することも確認する。
                with override_settings(DISCORD_BOT_TOKEN=''):
                    self.assertIs(send(), expected)
                payload = self.webhook.call_args.args[1]
                self.reset_posts()
                self.assertIs(send(), expected)
                self.post.assert_called_once()
                self.webhook.assert_not_called()
                self.assertEqual(self.post.call_args.kwargs['json'], payload)
                self.assertEqual(self.post.call_args.kwargs['headers']['Authorization'], f'Bot {settings.DISCORD_BOT_TOKEN}')
        self.community.refresh_from_db()
        self.assertEqual(self.community.notification_webhook_url, self.webhook_url)

    def test_bot_sends_all_six_even_without_webhook(self):
        self.community.notification_webhook_url = ''
        for name, send, expected in self.senders:
            with self.subTest(notification=name):
                self.reset_posts()
                self.assertIs(send(), expected)
                self.post.assert_called_once()
                self.webhook.assert_not_called()

    def assert_webhook(self):
        self.reset_posts()
        self.assertEqual(select_community_discord_delivery(self.community).kind, 'webhook')
        self.assertTrue(notifications.notify_video_article_published(self.detail))
        self.webhook.assert_called_once()
        self.post.assert_not_called()

    def test_missing_empty_or_non_matching_allowlist_and_missing_token_use_webhook(self):
        for overrides in (
            {'DISCORD_BOT_DELIVERY_COMMUNITY_IDS': ''},
            {'DISCORD_BOT_DELIVERY_COMMUNITY_IDS': ' , '},
            {'DISCORD_BOT_DELIVERY_COMMUNITY_IDS': str(self.community.pk + 1)},
            {'DISCORD_BOT_TOKEN': ''},
        ):
            with self.subTest(settings=list(overrides)), override_settings(**overrides):
                self.assert_webhook()

    def test_installed_removed_or_absent_record_uses_webhook(self):
        for status in ('installed', 'removed'):
            with self.subTest(status=status):
                BotInstallation.objects.filter(pk=self.installation.pk).update(status=status)
                self.assert_webhook()
        self.installation.delete()
        self.assert_webhook()

    def test_missing_or_invalid_channel_uses_webhook(self):
        invalid_ids = (None, '', '7' * 16, '7' * 21, 'x' * 18, '７' * 18, '٧' * 18,
                       ' ' + self.channel_id, self.channel_id + ' ', self.channel_id + '\n', '../messages')
        for index, value in enumerate(invalid_ids):
            with self.subTest(case=index):
                BotInstallation.objects.filter(pk=self.installation.pk).update(notification_channel_id=value)
                self.assert_webhook()

    def test_no_route_preserves_each_return_value_without_posting(self):
        self.community.notification_webhook_url = ''
        with override_settings(DISCORD_BOT_TOKEN=''):
            self.assertEqual(select_community_discord_delivery(self.community).kind, 'none')
            for name, send, expected in self.senders:
                with self.subTest(notification=name):
                    self.assertIs(send(), expected)
        self.post.assert_not_called()
        self.webhook.assert_not_called()

    def test_403_and_404_fall_back_for_each_notification_without_retrying_bot(self):
        for status in (403, 404):
            self.post.return_value = Mock(status_code=status)
            for name, send, expected in self.senders:
                with self.subTest(status=status, notification=name):
                    self.reset_posts()
                    with self.assertLogs('event.notifications', level='INFO') as logs:
                        self.assertIs(send(), expected)
                    self.post.assert_called_once()
                    self.webhook.assert_called_once()
                    fallbacks = [r for r in logs.records if getattr(r, 'event_type', '') == 'discord_bot_delivery_fallback']
                    self.assertEqual(len(fallbacks), 1)
                    self.assertEqual(fallbacks[0].count, 1)
                    self.assertEqual(fallbacks[0].status_code, status)
                    self.assertEqual(fallbacks[0].community_id, self.community.pk)
            # 永続状態と URL は変わらず、次回も Bot を試す。
            self.installation.refresh_from_db()
            self.assertEqual(self.installation.status, 'configured')
            self.assertEqual(select_community_discord_delivery(self.community).kind, 'bot')

    def test_403_and_404_without_webhook_do_not_send_elsewhere(self):
        self.community.notification_webhook_url = ''
        for status in (403, 404):
            self.post.return_value = Mock(status_code=status)
            for name, send, expected in self.senders:
                with self.subTest(status=status, notification=name):
                    self.reset_posts()
                    self.assertIs(send(), expected)
                    self.post.assert_called_once()
                    self.webhook.assert_not_called()

    def test_other_failures_retry_and_preserve_all_six_failure_returns_without_fallback(self):
        for status in (300, 400, 401, 429, 500):
            self.post.return_value = Mock(status_code=status)
            for name, send, expected in self.senders:
                with self.subTest(status=status, notification=name):
                    self.reset_posts()
                    self.assertIs(send(), False if expected is True else None)
                    self.assertEqual(self.post.call_count, 3)
                    self.webhook.assert_not_called()

    def test_fallback_failure_preserves_failure_returns(self):
        self.post.return_value = Mock(status_code=403)
        self.webhook.side_effect = requests.RequestException('synthetic webhook failure')
        for name, send, expected in self.senders:
            with self.subTest(notification=name):
                self.reset_posts()
                self.assertIs(send(), False if expected is True else None)
                self.post.assert_called_once()
                self.webhook.assert_called_once()

    def test_logs_do_not_expose_destinations_or_credentials(self):
        secrets = (settings.DISCORD_BOT_TOKEN, self.community.notification_webhook_url, self.channel_id)
        for outcome in (200, 403, 404, 500, 'timeout'):
            if outcome == 'timeout':
                self.post.side_effect = requests.Timeout(' '.join(secrets))
            else:
                self.post.return_value = Mock(status_code=outcome)
            for name, send, _ in self.senders:
                with self.subTest(outcome=outcome, notification=name), self.assertLogs(level='INFO') as logs:
                    send()
                rendered = '\n'.join(logs.output) + repr([r.__dict__ for r in logs.records])
                for secret in secrets:
                    self.assertNotIn(secret, rendered)
                self.assertNotIn('Traceback', rendered)
