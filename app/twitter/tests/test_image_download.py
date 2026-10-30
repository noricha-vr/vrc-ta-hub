"""X / Misskey 共通の画像取得は送信前に URL を検証する。"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from twitter.image_download import download_image


@patch('twitter.image_download.requests.get')
class ImageDownloadUrlTest(SimpleTestCase):
    def test_unsafe_urls_are_rejected_before_connecting(self, get):
        urls = [
            'http://data.vrc-ta-hub.com/image.png',
            'https://data.vrc-ta-hub.com:444/image.png',
            'https://data.vrc-ta-hub.com:invalid/image.png',
            'https://user@data.vrc-ta-hub.com/image.png',
            'https://user:pass@data.vrc-ta-hub.com/image.png',
            'https://data.vrc-ta-hub.com.evil.example/image.png',
            'https://[::1]/image.png',
            'not a URL',
        ]
        for scheme in ('http', 'https'):
            for host in ('127.0.0.1', 'localhost', 'internal', '2130706433', '0x7f000001', '127.0.0.1:8000'):
                urls.append(f'{scheme}://{host}\\@data.vrc-ta-hub.com/../admin')
                urls.append(f'{scheme}://{host}\\@data.vrc-ta-hub.com/image.png')
        for control in ('\t', '\n', '\r', '\x00', '\x1f', '\x7f'):
            urls.append(f'https://data.vrc-ta-hub.com/{control}image.png')
            urls.append(f'https://127.0.0.1\\{control}@data.vrc-ta-hub.com/../admin')
        for url in urls:
            with self.subTest(url=url):
                self.assertIsNone(download_image(url))
        get.assert_not_called()

    def test_only_prepared_and_validated_url_is_sent(self, get):
        response = MagicMock(status_code=200, headers={'Content-Type': 'image/png'})
        response.iter_content.return_value = [b'\x89PNG\r\n\x1a\nimage']
        get.return_value = response
        for authority in ('data.vrc-ta-hub.com', 'data.vrc-ta-hub.com:443'):
            with self.subTest(authority=authority):
                self.assertIsNotNone(download_image(f'https://{authority}/folder/../a b.png'))
                get.assert_called_with(f'https://{authority}/a%20b.png', timeout=30,
                                       stream=True, allow_redirects=False)

    def test_prepared_url_host_is_validated_again(self, get):
        prepared = MagicMock(url='https://127.0.0.1/admin')
        with patch('twitter.image_download.requests.Request') as request:
            request.return_value.prepare.return_value = prepared
            self.assertIsNone(download_image('https://data.vrc-ta-hub.com/image.png'))
        get.assert_not_called()
