"""QR コード PNG 生成ヘルパーのテスト。"""
from django.test import TestCase

from analytics.qr_generator import generate_qr_png


class GenerateQrPngTest(TestCase):
    def test_returns_png_content_file(self):
        cf = generate_qr_png('https://vrc-ta-hub.example/?utm_campaign=test')
        # PNG マジックバイト
        self.assertTrue(cf.read(8).startswith(b'\x89PNG\r\n\x1a\n'))
        # ファイル名は <uuid>.png 形式
        self.assertTrue(cf.name.endswith('.png'))

    def test_unique_filename_per_call(self):
        a = generate_qr_png('https://example.com/a')
        b = generate_qr_png('https://example.com/b')
        self.assertNotEqual(a.name, b.name)
