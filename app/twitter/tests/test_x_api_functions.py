"""X API投稿と画像アップロードのテスト。"""

from unittest.mock import MagicMock, patch
from django.test import TestCase, tag


@tag('offline_external_api')
class PostTweetFunctionTest(TestCase):
    """X API 投稿関数の単体テスト（OAuth 1.0a）"""

    OAUTH1_ENV = {
        "X_API_KEY": "test-api-key",
        "X_API_SECRET": "test-api-secret",
        "X_ACCESS_TOKEN": "test-access-token",
        "X_ACCESS_TOKEN_SECRET": "test-access-token-secret",
        "X_API_ALLOW_TEST_CALLS": "1",
    }

    @patch("twitter.x_api.requests.post")
    def test_post_tweet_success(self, mock_post):
        """正常にツイートが投稿される"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {"id": "12345", "text": "テストツイート"},
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import post_tweet
            result = post_tweet("テストツイート")

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["id"], "12345")

        # OAuth1 認証が使われていることを確認
        call_kwargs = mock_post.call_args
        self.assertIsNotNone(call_kwargs.kwargs.get("auth"))

    @patch("twitter.x_api.requests.post")
    def test_post_tweet_with_media_ids(self, mock_post):
        """media_ids 付きでツイートが投稿される"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {"id": "12345", "text": "画像付き"},
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import post_tweet
            result = post_tweet("画像付き", media_ids=["media_111"])

        self.assertTrue(result["ok"])
        # payload に media フィールドが含まれている
        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json")
        self.assertEqual(payload["media"], {"media_ids": ["media_111"]})

    def test_post_tweet_no_credentials(self):
        """環境変数が未設定の場合は ok=False を返す"""
        with patch.dict("os.environ", {
            "X_API_KEY": "",
            "X_API_SECRET": "",
            "X_ACCESS_TOKEN": "",
            "X_ACCESS_TOKEN_SECRET": "",
        }):
            from twitter.x_api import post_tweet
            result = post_tweet("テスト")
        self.assertFalse(result["ok"])
        self.assertIsNone(result["data"])

    @patch("twitter.x_api.requests.post")
    def test_post_tweet_api_error(self, mock_post):
        """API エラー時は ok=False を返す"""
        import requests

        mock_post.side_effect = requests.RequestException("API Error")

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import post_tweet
            result = post_tweet("テスト")
        self.assertFalse(result["ok"])

    @patch("twitter.x_api.requests.post")
    def test_post_tweet_api_error_with_response(self, mock_post):
        """API エラー時にレスポンスがある場合は status_code/error_body を返す"""
        import requests

        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = '{"detail": "You are not permitted to perform this action."}'
        error = requests.RequestException("Forbidden")
        error.response = mock_response
        mock_post.side_effect = error

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import post_tweet
            with self.assertLogs("twitter.x_api", level="ERROR") as log_ctx:
                result = post_tweet("テスト")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status_code"], 403)
        self.assertIn("not permitted", result["error_body"])
        combined = "\n".join(log_ctx.output)
        self.assertIn("403", combined)
        self.assertIn("not permitted", combined)

    def test_post_tweet_missing_partial_credentials(self):
        """一部の認証情報だけ設定されている場合は ok=False を返す"""
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
        """Python の len が280以内でも重み付き超過なら X API を呼ばない"""
        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import post_tweet
            result = post_tweet("あ" * 141)

        self.assertFalse(result["ok"])
        self.assertIn("weighted_length", result["error_body"])
        mock_post.assert_not_called()


@tag('offline_external_api')
class UploadMediaFunctionTest(TestCase):
    """upload_media 関数のテスト"""

    OAUTH1_ENV = {
        "X_API_KEY": "test-api-key",
        "X_API_SECRET": "test-api-secret",
        "X_ACCESS_TOKEN": "test-access-token",
        "X_ACCESS_TOKEN_SECRET": "test-access-token-secret",
        "X_API_ALLOW_TEST_CALLS": "1",
    }
    ALLOWED_IMAGE_URL = "https://data.vrc-ta-hub.com/community/1/poster.webp"

    def _make_stream_response(self, data=None, content_type="image/webp"):
        """stream=True のレスポンスモックを生成するヘルパー"""
        if data is None:
            data = b"RIFF\x04\x00\x00\x00WEBP"
        mock_response = MagicMock()
        mock_response.headers = {"Content-Type": content_type}
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_content = MagicMock(return_value=[data])
        return mock_response

    @patch("twitter.x_api.requests.post")
    @patch("twitter.x_api.requests.get")
    def test_upload_media_success(self, mock_get, mock_post):
        """正常に画像がアップロードされる"""
        mock_get.return_value = self._make_stream_response()

        # メディアアップロードのモック
        mock_upload_response = MagicMock()
        mock_upload_response.json.return_value = {"media_id_string": "media_12345"}
        mock_upload_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_upload_response

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media
            result = upload_media(self.ALLOWED_IMAGE_URL)

        self.assertEqual(result, "media_12345")

    @patch("twitter.x_api.requests.get")
    def test_upload_media_download_failure(self, mock_get):
        """画像ダウンロード失敗時は None を返す"""
        import requests

        mock_get.side_effect = requests.RequestException("Download failed")

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media
            result = upload_media(self.ALLOWED_IMAGE_URL)

        self.assertIsNone(result)

    def test_upload_media_no_credentials(self):
        """認証情報がない場合は None を返す"""
        with patch.dict("os.environ", {
            "X_API_KEY": "",
            "X_API_SECRET": "",
            "X_ACCESS_TOKEN": "",
            "X_ACCESS_TOKEN_SECRET": "",
        }):
            from twitter.x_api import upload_media
            result = upload_media(self.ALLOWED_IMAGE_URL)
        self.assertIsNone(result)

    @patch("twitter.x_api.requests.post")
    @patch("twitter.x_api.requests.get")
    def test_upload_media_upload_failure(self, mock_get, mock_post):
        """X API へのアップロード失敗時は None を返す"""
        import requests

        mock_get.return_value = self._make_stream_response(content_type="image/png")
        mock_post.side_effect = requests.RequestException("Upload failed")

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media
            result = upload_media(self.ALLOWED_IMAGE_URL)

        self.assertIsNone(result)

    @patch("twitter.x_api.requests.post")
    @patch("twitter.x_api.requests.get")
    def test_upload_media_upload_failure_with_response(self, mock_get, mock_post):
        """X API アップロード失敗時にレスポンスがある場合はステータスとボディをログ出力する"""
        import requests

        mock_get.return_value = self._make_stream_response(content_type="image/png")
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = '{"errors": [{"message": "media type not allowed"}]}'
        error = requests.RequestException("Forbidden")
        error.response = mock_response
        mock_post.side_effect = error

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media
            with self.assertLogs("twitter.x_api", level="ERROR") as log_ctx:
                result = upload_media(self.ALLOWED_IMAGE_URL)

        self.assertIsNone(result)
        combined = "\n".join(log_ctx.output)
        self.assertIn("403", combined)
        self.assertIn("media type not allowed", combined)

    # --- SSRF防止テスト ---
    def test_upload_media_blocks_untrusted_domain(self):
        """許可リストにないドメインからの画像ダウンロードを拒否する"""
        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media
            result = upload_media("https://evil.example.com/malicious.png")
        self.assertIsNone(result)

    def test_upload_media_blocks_localhost(self):
        """localhost からの画像ダウンロードを拒否する"""
        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media
            result = upload_media("http://localhost:8080/internal-api")
        self.assertIsNone(result)

    def test_upload_media_blocks_internal_ip(self):
        """内部IPアドレスからの画像ダウンロードを拒否する"""
        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media
            result = upload_media("http://169.254.169.254/latest/meta-data/")
        self.assertIsNone(result)

    @patch("twitter.x_api.requests.post")
    @patch("twitter.x_api.requests.get")
    def test_upload_media_allows_trusted_domain(self, mock_get, mock_post):
        """許可ドメインからのダウンロードは成功する"""
        mock_get.return_value = self._make_stream_response()

        mock_upload_response = MagicMock()
        mock_upload_response.json.return_value = {"media_id_string": "media_ok"}
        mock_upload_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_upload_response

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media
            result = upload_media("https://data.vrc-ta-hub.com/poster.webp")

        self.assertEqual(result, "media_ok")

    @patch("twitter.x_api.requests.post")
    @patch("twitter.x_api.requests.get")
    def test_upload_media_allows_cf_transform_url(self, mock_get, mock_post):
        """CF Image Resizing URL も許可ドメインとして通過する"""
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

    # --- サイズ制限テスト ---
    @patch("twitter.x_api.requests.get")
    def test_upload_media_rejects_oversized_image(self, mock_get):
        """5MB超の画像は拒否される"""
        # 6MB のチャンクを返すモック
        oversized_data = b"x" * (6 * 1024 * 1024)
        mock_get.return_value = self._make_stream_response(data=oversized_data)

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media
            result = upload_media(self.ALLOWED_IMAGE_URL)

        self.assertIsNone(result)

    @patch("twitter.x_api.requests.get")
    def test_upload_media_rejects_oversized_chunked_image(self, mock_get):
        """複数チャンクで合計5MB超の場合も拒否される"""
        chunk_size = 1024 * 1024  # 1MB per chunk
        chunks = [b"x" * chunk_size for _ in range(6)]  # 6MB total

        mock_response = MagicMock()
        mock_response.headers = {"Content-Type": "image/png"}
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_content = MagicMock(return_value=iter(chunks))
        mock_get.return_value = mock_response

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media
            result = upload_media(self.ALLOWED_IMAGE_URL)

        self.assertIsNone(result)

    @patch("twitter.x_api.requests.post")
    @patch("twitter.x_api.requests.get")
    def test_upload_media_accepts_exactly_5mb(self, mock_get, mock_post):
        """ちょうど5MBの画像は受け入れられる"""
        exactly_5mb = b"\x89PNG\r\n\x1a\n" + b"x" * (5 * 1024 * 1024 - 8)
        mock_get.return_value = self._make_stream_response(data=exactly_5mb)

        mock_upload_response = MagicMock()
        mock_upload_response.json.return_value = {"media_id_string": "media_5mb"}
        mock_upload_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_upload_response

        with patch.dict("os.environ", self.OAUTH1_ENV):
            from twitter.x_api import upload_media
            result = upload_media(self.ALLOWED_IMAGE_URL)

        self.assertEqual(result, "media_5mb")
