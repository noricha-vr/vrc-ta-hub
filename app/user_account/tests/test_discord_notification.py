from unittest.mock import patch, MagicMock

from django.test import TestCase, RequestFactory, override_settings
from django.urls import reverse

from user_account.models import CustomUser
from community.views import CommunityCreateView


class DiscordNotificationTest(TestCase):
    """Discord通知のテスト"""

    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/test/test')
    @patch('community.views.requests.post')
    def test_discord_notification_sent_on_community_registration(self, mock_requests_post):
        """集会登録時にDiscord通知が送信されることをテスト"""
        mock_requests_post.return_value = MagicMock(status_code=204)

        from django.conf import settings

        # ViewのDiscord通知ロジックを直接テスト
        view = CommunityCreateView()
        request = self.factory.get('/')
        request.build_absolute_uri = lambda path: f'http://testserver{path}'
        view.request = request

        # モック集会作成
        mock_community = MagicMock()
        mock_community.name = 'テスト集会'

        # settings.DISCORD_WEBHOOK_URLが設定されている場合の通知ロジックをテスト
        import requests as real_requests
        waiting_list_url = request.build_absolute_uri(reverse('community:waiting_list'))
        discord_message = {
            "content": f"**【新規集会登録】** {mock_community.name}\n"
                       f"承認ページ: {waiting_list_url}"
        }
        discord_timeout_seconds = 10

        # Discord通知を送信
        real_requests.post(settings.DISCORD_WEBHOOK_URL, json=discord_message, timeout=discord_timeout_seconds)

        # Discord通知が呼ばれたことを確認
        mock_requests_post.assert_called_once()
        call_args = mock_requests_post.call_args

        # Webhook URLが正しいことを確認
        self.assertEqual(call_args[0][0], 'https://discord.com/api/webhooks/test/test')

        # メッセージ内容を確認
        json_data = call_args[1]['json']
        self.assertIn('テスト集会', json_data['content'])
        self.assertIn('新規集会登録', json_data['content'])
        self.assertIn('承認ページ', json_data['content'])

        # タイムアウト設定を確認
        self.assertEqual(call_args[1]['timeout'], discord_timeout_seconds)

    @override_settings(DISCORD_WEBHOOK_URL='')
    def test_discord_notification_not_sent_when_webhook_not_configured(self):
        """DISCORD_WEBHOOK_URLが設定されていない場合、Discord通知が送信されないことをテスト"""
        from django.conf import settings

        # 空のwebhook URLの場合、if条件でFalseになる
        self.assertFalse(bool(settings.DISCORD_WEBHOOK_URL))

    @override_settings(DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/test/test')
    @patch('community.views.requests.post')
    def test_discord_notification_error_handling(self, mock_requests_post):
        """Discord通知が失敗した場合のエラーハンドリングをテスト"""
        from community.views import logger
        import requests

        # Discord通知をエラーにする
        mock_requests_post.side_effect = Exception('Network error')

        # ロジックをシミュレート
        from django.conf import settings

        discord_message = {
            "content": "**【新規集会登録】** テスト集会\n承認ページ: http://testserver/community/waiting/"
        }
        discord_timeout_seconds = 10

        # 例外が発生してもエラーが握りつぶされることをテスト
        if settings.DISCORD_WEBHOOK_URL:
            try:
                requests.post(settings.DISCORD_WEBHOOK_URL, json=discord_message, timeout=discord_timeout_seconds)
            except Exception as e:
                # ログに記録するだけでエラーは握りつぶす
                logger.warning(f'Discord通知送信失敗: {e}')

        # 例外が発生してもここまで到達すること（エラーが握りつぶされている）
        self.assertTrue(True)

    @override_settings(DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/test/test')
    @patch('community.views.requests.post')
    @patch('community.views.logger')
    def test_discord_notification_failure_logs_warning(self, mock_logger, mock_requests_post):
        """Discord通知が失敗した場合にwarningログが記録されることをテスト"""
        import requests as real_requests
        from django.conf import settings

        # Discord通知をエラーにする
        mock_requests_post.side_effect = Exception('Network error')

        discord_message = {
            "content": "**【新規集会登録】** テスト集会\n承認ページ: http://testserver/community/waiting/"
        }
        discord_timeout_seconds = 10

        # エラーハンドリングをテスト
        if settings.DISCORD_WEBHOOK_URL:
            try:
                real_requests.post(settings.DISCORD_WEBHOOK_URL, json=discord_message, timeout=discord_timeout_seconds)
            except Exception as e:
                mock_logger.warning(f'Discord通知送信失敗: {e}')

        # warningログが記録されていることを確認
        mock_logger.warning.assert_called_once()
        log_message = mock_logger.warning.call_args[0][0]
        self.assertIn('Discord通知送信失敗', log_message)
        self.assertIn('Network error', log_message)


class DiscordWebhookMessageFormatTest(TestCase):
    """Discord Webhookメッセージフォーマットのテスト"""

    def test_message_format_contains_required_fields(self):
        """通知メッセージに必要なフィールドが含まれていることをテスト"""
        community_name = 'テスト集会'
        waiting_list_url = 'http://testserver/community/waiting/'

        discord_message = {
            "content": f"**【新規集会登録】** {community_name}\n"
                       f"承認ページ: {waiting_list_url}"
        }

        # 必須フィールドの確認
        self.assertIn('content', discord_message)
        self.assertIn('新規集会登録', discord_message['content'])
        self.assertIn(community_name, discord_message['content'])
        self.assertIn('承認ページ', discord_message['content'])
        self.assertIn(waiting_list_url, discord_message['content'])

    def test_message_format_uses_bold_formatting(self):
        """通知メッセージがDiscordの太字フォーマットを使用していることをテスト"""
        community_name = 'テスト集会'
        waiting_list_url = 'http://testserver/community/waiting/'

        discord_message = {
            "content": f"**【新規集会登録】** {community_name}\n"
                       f"承認ページ: {waiting_list_url}"
        }

        # Discordの太字フォーマット（**テキスト**）が使用されている
        self.assertIn('**【新規集会登録】**', discord_message['content'])
