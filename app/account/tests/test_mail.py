from django.test import SimpleTestCase
from django.core.mail import send_mail
from django.conf import settings


class EmailTest(SimpleTestCase):
    def test_send_email(self):
        """実際のメール送信のテスト"""
        subject = 'VRC技術学術系Hub メール送信テスト'
        message = 'これはAWS SESを使用した送信テストメールです。'
        from_email = settings.DEFAULT_FROM_EMAIL
        recipient_list = ['noricha.vr@gmail.com']

        # メール送信
        result = send_mail(
            subject=subject,
            message=message,
            from_email=from_email,
            recipient_list=recipient_list,
        )

        # 送信成功の確認（1は送信成功したメールの数）
        self.assertEqual(result, 1)

    def test_send_html_email(self):
        """実際のHTMLメール送信のテスト"""
        subject = 'VRC技術学術系Hub HTMLメール送信テスト'
        message = 'これはプレーンテキストバージョンです。'
        html_message = '''
        <h1>VRC技術学術系Hub メール送信テスト</h1>
        <p>これはAWS SESを使用したHTML形式の送信テストメールです。</p>
        <ul>
            <li>HTML形式のメールが正しく送信されるかのテスト</li>
            <li>スタイリングやリンクが正しく機能するかのテスト</li>
        </ul>
        <p><a href="https://vrc-ta-hub.com">VRC技術学術系Hub</a></p>
        '''
        from_email = settings.DEFAULT_FROM_EMAIL
        recipient_list = ['noricha.vr@gmail.com']

        # メール送信
        result = send_mail(
            subject=subject,
            message=message,
            from_email=from_email,
            recipient_list=recipient_list,
            html_message=html_message,
        )

        # 送信成功の確認
        self.assertEqual(result, 1) 
