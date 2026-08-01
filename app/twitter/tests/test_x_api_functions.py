"""X API正本テストにない認証・SSRF・サイズ境界のテスト。"""

from unittest.mock import MagicMock, patch

import requests
from django.test import TestCase, tag


@tag('offline_external_api')
class PostTweetFunctionTest(TestCase):
    """投稿前の認証・重み付き文字数検証を補完する。"""

    OAUTH1_ENV = {
        "X_API_KEY": "test-api-key",
        "X_API_SECRET": "test-api-secret",
        "X_ACCESS_TOKEN": "test-access-token",
        "X_ACCESS_TOKEN_SECRET": "test-access-token-secret",
        "X_API_ALLOW_TEST_CALLS": "1",
    }

    def test_post_tweet_missing_partial_credentials(self):
        """一部の認証情報だけ設定されている場合は ok=False を返す。"""
        with patch.dict("os.environ", {
            "X_API_KEY": "key",
            "X_API_SECRET": "secret",
            "X_ACCESS_TOKEN": "",
            "X_ACCESS_TOKEN_SECRET": "",
        }):
            from twitter.x_api import post_tweet

            result = post_tweet("テスト")

        self.assertFalse(result["ok"])

    @patch("twitter.x_api.requests.post")
    def test_post_tweet_rejects_weighted_length_before_api_call(self, mock_post):
        """重み付き文字数が超過する投稿は X API を呼ばない。"""
        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import post_tweet

            result = post_tweet("あ" * 141)

        self.assertFalse(result["ok"])
        self.assertIn("weighted_length", result["error_body"])
        mock_post.assert_not_called()


@tag('offline_external_api')
class UploadMediaFunctionTest(TestCase):
    """正本テストと重複しない画像アップロード境界を補完する。"""

    OAUTH1_ENV = {
        "X_API_KEY": "test-api-key",
        "X_API_SECRET": "test-api-secret",
        "X_ACCESS_TOKEN": "test-access-token",
        "X_ACCESS_TOKEN_SECRET": "test-access-token-secret",
        "X_API_ALLOW_TEST_CALLS": "1",
    }
    ALLOWED_IMAGE_URL = "https://data.vrc-ta-hub.com/community/1/poster.webp"

    @staticmethod
    def _make_stream_response(data=None, content_type="image/webp"):
        """stream=True のレスポンスモックを返す。"""
        if data is None:
            data = b"RIFF\x04\x00\x00\x00WEBP"
        mock_response = MagicMock()
        mock_response.headers = {"Content-Type": content_type}
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_content = MagicMock(return_value=[data])
        return mock_response

    @patch("twitter.x_api.requests.post")
    @patch("twitter.x_api.requests.get")
    def test_upload_media_upload_failure(self, mock_get, mock_post):
        """レスポンスを伴わないアップロード失敗は None を返す。"""
        mock_get.return_value = self._make_stream_response(content_type="image/png")
        mock_post.side_effect = requests.RequestException("Upload failed")

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media

            result = upload_media(self.ALLOWED_IMAGE_URL)

        self.assertIsNone(result)

    def test_upload_media_blocks_localhost(self):
        """localhost からの画像ダウンロードを拒否する。"""
        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media

            result = upload_media("http://localhost:8080/internal-api")

        self.assertIsNone(result)

    def test_upload_media_blocks_internal_ip(self):
        """内部IPアドレスからの画像ダウンロードを拒否する。"""
        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media

            result = upload_media("http://169.254.169.254/latest/meta-data/")

        self.assertIsNone(result)

    @patch("twitter.x_api.requests.post")
    @patch("twitter.x_api.requests.get")
    def test_upload_media_allows_cf_transform_url(self, mock_get, mock_post):
        """CF Image Resizing URL も許可ドメインとして通過する。"""
        mock_get.return_value = self._make_stream_response()
        mock_upload_response = MagicMock()
        mock_upload_response.json.return_value = {"media_id_string": "media_cf"}
        mock_upload_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_upload_response
        cf_url = (
            "https://data.vrc-ta-hub.com/cdn-cgi/image/"
            "width=960,quality=80,format=auto/community/1/poster.webp"
        )

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media

            result = upload_media(cf_url)

        self.assertEqual(result, "media_cf")

    @patch("twitter.x_api.requests.get")
    def test_upload_media_rejects_oversized_chunked_image(self, mock_get):
        """複数チャンクの合計が5MBを超える画像を拒否する。"""
        chunk_size = 1024 * 1024
        chunks = [b"x" * chunk_size for _ in range(6)]
        mock_response = MagicMock()
        mock_response.headers = {"Content-Type": "image/png"}
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_content = MagicMock(return_value=iter(chunks))
        mock_get.return_value = mock_response

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media

            result = upload_media(self.ALLOWED_IMAGE_URL)

        self.assertIsNone(result)
