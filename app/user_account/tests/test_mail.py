import logging
import os
import sys
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.mail import send_mail
from django.test import SimpleTestCase, override_settings, tag

# ロガーの設定
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

@tag('offline_external_api')
class EmailTest(SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.email_directory = tempfile.TemporaryDirectory(prefix='vrc-ta-hub-email-')
        self.email_path = Path(self.email_directory.name)
        self.settings_override = override_settings(
            EMAIL_BACKEND='django.core.mail.backends.filebased.EmailBackend',
            EMAIL_FILE_PATH=str(self.email_path),
        )
        self.settings_override.enable()
        self.addCleanup(self.email_directory.cleanup)
        self.addCleanup(self.settings_override.disable)
        print("\nTest setup completed")
        print(f"Email backend: {settings.EMAIL_BACKEND}")
        print(f"Email file path: {settings.EMAIL_FILE_PATH}")

    def tearDown(self):
        # テスト終了後にメールファイルを表示
        print("\nGenerated email files:")
        for file in os.listdir(self.email_path):
            file_path = self.email_path / file
            print(f"\nReading email file: {file}")
            try:
                # Try UTF-8 first
                with open(file_path, 'r', encoding='utf-8') as f:
                    print(f.read())
            except UnicodeDecodeError:
                try:
                    # If UTF-8 fails, try with cp932 (Japanese encoding)
                    with open(file_path, 'r', encoding='cp932') as f:
                        print(f.read())
                except UnicodeDecodeError:
                    # If both fail, read as binary and print hex
                    with open(file_path, 'rb') as f:
                        print("Binary content (hex):")
                        print(f.read().hex())
        super().tearDown()

    def test_email_path_is_a_writable_temporary_directory(self):
        """非root環境でも書き込めるテスト固有ディレクトリを使用する。"""
        self.assertTrue(self.email_path.is_dir())
        self.assertTrue(os.access(self.email_path, os.W_OK))
        self.assertNotEqual(self.email_path, Path('/app/test-emails'))

    def test_send_welcome_email(self):
        """ウェルカムメールのテスト"""
        print("\nStarting welcome email test")
        logger.debug('Starting welcome email test')

        subject = 'VRC技術学術系Hub へようこそ！'
        message = 'これはプレーンテキストバージョンです。'
        from_email = settings.DEFAULT_FROM_EMAIL
        recipient_list = ['test@example.com']

        context = {
            'user_name': 'テストユーザー',
            'login_url': 'https://vrc-ta-hub.com/login',
            'discord_url': 'https://discord.gg/example'
        }

        # テンプレートの読み込み
        from django.template.loader import render_to_string
        html_message = render_to_string('account/email/welcome.html', context)

        try:
            result = send_mail(
                subject=subject,
                message=message,
                from_email=from_email,
                recipient_list=recipient_list,
                html_message=html_message,
                fail_silently=False,
            )
            logger.info(f'Welcome email sent successfully: {result}')
            print(f'Welcome email sent successfully: {result}')
        except Exception as e:
            logger.error(f'Failed to send welcome email: {str(e)}')
            print(f'Failed to send welcome email: {str(e)}')
            raise

        self.assertEqual(result, 1)

    def test_send_accept_email(self):
        """コミュニティ承認メールのテスト"""
        print("\nStarting accept email test")
        logger.debug('Starting accept email test')

        subject = '集会が承認されました'
        message = 'これはプレーンテキストバージョンです。'
        from_email = settings.DEFAULT_FROM_EMAIL
        recipient_list = ['test@example.com']

        # テスト用のコンテキストデータ
        context = {
            'community': {
                'name': 'テストコミュニティ',
                'custom_user': {
                    'user_name': 'テストユーザー'
                }
            },
            'my_list_url': 'https://vrc-ta-hub.com/event/my_list'
        }

        # テンプレートの読み込み
        from django.template.loader import render_to_string
        html_message = render_to_string('community/email/accept.html', context)

        try:
            result = send_mail(
                subject=subject,
                message=message,
                from_email=from_email,
                recipient_list=recipient_list,
                html_message=html_message,
                fail_silently=False,
            )
            logger.info(f'Accept email sent successfully: {result}')
            print(f'Accept email sent successfully: {result}')
        except Exception as e:
            logger.error(f'Failed to send accept email: {str(e)}')
            print(f'Failed to send accept email: {str(e)}')
            raise

        self.assertEqual(result, 1)
