import logging
from django.test import SimpleTestCase
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.conf import settings

logger = logging.getLogger(__name__)

class EmailTest(SimpleTestCase):
    def test_welcome_email(self):
        """ウェルカムメールの送信テスト"""
        try:
            context = {
                'user_name': 'テストユーザー',
                'login_url': 'http://0.0.0.0:8015/login/',
                'discord_url': 'https://discord.gg/your-invite-link'
            }
            html_message = render_to_string('account/email/welcome.html', context)
            
            logger.info(f"AWS SES設定: Region={settings.AWS_SES_REGION_NAME}")
            logger.info(f"AWS SES認証情報: Access Key ID={settings.AWS_SES_ACCESS_KEY_ID}")
            logger.info(f"送信元アドレス: {settings.DEFAULT_FROM_EMAIL}")
            
            result = send_mail(
                subject='VRC技術学術系Hub へようこそ！',
                message='',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=['noricha.vr@gmail.com'],
                html_message=html_message
            )
            logger.info(f"メール送信結果: {result}")
            self.assertEqual(result, 1)
        except Exception as e:
            logger.error(f"メール送信エラー: {str(e)}")
            raise

    def test_accept_email(self):
        """コミュニティ承認メールの送信テスト"""
        try:
            context = {
                'community': {
                    'name': 'テストコミュニティ',
                    'custom_user': {'user_name': 'テストユーザー'}
                },
                'my_list_url': 'http://0.0.0.0:8015/event/my_list/'
            }
            html_message = render_to_string('community/email/accept.html', context)
            
            logger.info(f"AWS SES設定: Region={settings.AWS_SES_REGION_NAME}")
            logger.info(f"AWS SES認証情報: Access Key ID={settings.AWS_SES_ACCESS_KEY_ID}")
            logger.info(f"送信元アドレス: {settings.DEFAULT_FROM_EMAIL}")
            
            result = send_mail(
                subject='テストコミュニティが承認されました',
                message='',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=['noricha.vr@gmail.com'],
                html_message=html_message
            )
            logger.info(f"メール送信結果: {result}")
            self.assertEqual(result, 1)
        except Exception as e:
            logger.error(f"メール送信エラー: {str(e)}")
            raise 
