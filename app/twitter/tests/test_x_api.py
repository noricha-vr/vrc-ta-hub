"""twitter.x_api の単体テスト

実ネットワーク呼び出しパス（stream chunk over MAX_IMAGE_SIZE、SSRF ドメインチェック、
JSON デコード失敗、timeout）が CI でカバーされていなかったので追加する。

既存 test_x_api_guard.py は TESTING=True ガード自体の検証に特化しているため、
本ファイルは X_API_ALLOW_TEST_CALLS=1 で API パスをモックして網羅する。
"""
from unittest.mock import MagicMock, patch

import requests
from django.test import TestCase, override_settings

from twitter.x_api import (
    MAX_IMAGE_SIZE,
    _get_oauth1,
    post_tweet,
    upload_media,
)


VALID_CREDS_ENV = {
    "X_API_KEY": "k",
    "X_API_SECRET": "s",
    "X_ACCESS_TOKEN": "t",
    "X_ACCESS_TOKEN_SECRET": "ts",
    "X_API_ALLOW_TEST_CALLS": "1",
}


class GetOAuth1Test(TestCase):
    """_get_oauth1 の認証情報チェック"""

    @patch.dict("os.environ", VALID_CREDS_ENV, clear=False)
    def test_returns_oauth1_when_all_credentials_present(self):
        """全 credential 揃っていれば OAuth1 オブジェクトを返す"""
        auth = _get_oauth1()
        self.assertIsNotNone(auth)

    @patch.dict("os.environ", {
        "X_API_KEY": "k",
        "X_API_SECRET": "",
        "X_ACCESS_TOKEN": "t",
        "X_ACCESS_TOKEN_SECRET": "ts",
    }, clear=False)
    def test_returns_none_when_secret_missing(self):
        """X_API_SECRET が空なら None"""
        auth = _get_oauth1()
        self.assertIsNone(auth)

    @patch.dict("os.environ", {
        "X_API_KEY": "",
        "X_API_SECRET": "s",
        "X_ACCESS_TOKEN": "t",
        "X_ACCESS_TOKEN_SECRET": "ts",
    }, clear=False)
    def test_returns_none_when_api_key_missing(self):
        """X_API_KEY が空なら None"""
        auth = _get_oauth1()
        self.assertIsNone(auth)


class UploadMediaSsrfTest(TestCase):
    """upload_media の SSRF ドメインチェック"""

    @patch.dict("os.environ", VALID_CREDS_ENV, clear=False)
    @patch("twitter.x_api.requests.get")
    def test_blocks_untrusted_domain(self, mock_get):
        """ALLOWED_IMAGE_DOMAINS 外のホストはダウンロードせず None"""
        result = upload_media("https://evil.example.com/image.png")
        self.assertIsNone(result)
        mock_get.assert_not_called()

    @patch.dict("os.environ", VALID_CREDS_ENV, clear=False)
    @patch("twitter.x_api.requests.post")
    @patch("twitter.x_api.requests.get")
    def test_allows_trusted_domain(self, mock_get, mock_post):
        """data.vrc-ta-hub.com からのダウンロードは通す"""
        mock_get_response = MagicMock()
        mock_get_response.raise_for_status.return_value = None
        mock_get_response.iter_content.return_value = [b"\x89PNG\r\n\x1a\n"]
        mock_get_response.headers = {"Content-Type": "image/png"}
        mock_get.return_value = mock_get_response

        mock_post_response = MagicMock()
        mock_post_response.raise_for_status.return_value = None
        mock_post_response.json.return_value = {"media_id_string": "12345"}
        mock_post.return_value = mock_post_response

        result = upload_media("https://data.vrc-ta-hub.com/test.png")
        self.assertEqual(result, "12345")
        mock_get.assert_called_once()


class UploadMediaSizeLimitTest(TestCase):
    """upload_media の MAX_IMAGE_SIZE 強制"""

    @patch.dict("os.environ", VALID_CREDS_ENV, clear=False)
    @patch("twitter.x_api.requests.post")
    @patch("twitter.x_api.requests.get")
    def test_returns_none_when_image_exceeds_max_size(self, mock_get, mock_post):
        """5MB 超のダウンロードを中断して None を返す（OOM 防止）"""
        # MAX_IMAGE_SIZE を 1 バイト超えるチャンクを返す
        oversized_chunk = b"a" * (MAX_IMAGE_SIZE + 1)
        mock_get_response = MagicMock()
        mock_get_response.raise_for_status.return_value = None
        mock_get_response.iter_content.return_value = [oversized_chunk]
        mock_get.return_value = mock_get_response

        result = upload_media("https://data.vrc-ta-hub.com/big.png")
        self.assertIsNone(result)
        mock_post.assert_not_called()

    @patch.dict("os.environ", VALID_CREDS_ENV, clear=False)
    @patch("twitter.x_api.requests.post")
    @patch("twitter.x_api.requests.get")
    def test_accepts_image_at_max_size(self, mock_get, mock_post):
        """ちょうど MAX_IMAGE_SIZE の画像は通す（境界値）"""
        boundary_chunks = [b"a" * MAX_IMAGE_SIZE]
        mock_get_response = MagicMock()
        mock_get_response.raise_for_status.return_value = None
        mock_get_response.iter_content.return_value = boundary_chunks
        mock_get_response.headers = {"Content-Type": "image/png"}
        mock_get.return_value = mock_get_response

        mock_post_response = MagicMock()
        mock_post_response.raise_for_status.return_value = None
        mock_post_response.json.return_value = {"media_id_string": "ok"}
        mock_post.return_value = mock_post_response

        result = upload_media("https://data.vrc-ta-hub.com/exact.png")
        self.assertEqual(result, "ok")


class UploadMediaErrorHandlingTest(TestCase):
    """upload_media の RequestException ハンドリング"""

    @patch.dict("os.environ", VALID_CREDS_ENV, clear=False)
    @patch("twitter.x_api.requests.get")
    def test_returns_none_on_download_request_exception(self, mock_get):
        """ダウンロード時の RequestException で None を返す（silent失敗）"""
        mock_get.side_effect = requests.RequestException("connection refused")
        result = upload_media("https://data.vrc-ta-hub.com/x.png")
        self.assertIsNone(result)

    @patch.dict("os.environ", VALID_CREDS_ENV, clear=False)
    @patch("twitter.x_api.requests.post")
    @patch("twitter.x_api.requests.get")
    def test_returns_none_on_upload_response_4xx(self, mock_get, mock_post):
        """アップロード時の 4xx 応答で raise_for_status → None"""
        mock_get_response = MagicMock()
        mock_get_response.raise_for_status.return_value = None
        mock_get_response.iter_content.return_value = [b"img"]
        mock_get_response.headers = {"Content-Type": "image/png"}
        mock_get.return_value = mock_get_response

        mock_post_response = MagicMock()
        mock_post_response.status_code = 401
        mock_post_response.text = '{"errors":[{"code":89}]}'
        mock_post_response.raise_for_status.side_effect = requests.HTTPError(response=mock_post_response)
        mock_post.return_value = mock_post_response

        result = upload_media("https://data.vrc-ta-hub.com/x.png")
        self.assertIsNone(result)

    @patch.dict("os.environ", {
        "X_API_KEY": "",
        "X_API_SECRET": "",
        "X_ACCESS_TOKEN": "",
        "X_ACCESS_TOKEN_SECRET": "",
        "X_API_ALLOW_TEST_CALLS": "1",
    }, clear=False)
    def test_returns_none_when_credentials_missing(self):
        """credential 不足で None"""
        result = upload_media("https://data.vrc-ta-hub.com/x.png")
        self.assertIsNone(result)


class PostTweetValidationTest(TestCase):
    """post_tweet の入力バリデーション"""

    @patch.dict("os.environ", VALID_CREDS_ENV, clear=False)
    def test_empty_text_returns_failure(self):
        """空文字テキストは ok=False"""
        result = post_tweet("")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_body"], "Tweet text is empty")

    @patch.dict("os.environ", VALID_CREDS_ENV, clear=False)
    @patch("twitter.x_api.validate_tweet_text")
    def test_local_validation_failure_returns_failure(self, mock_validate):
        """ローカルバリデーション失敗時は ok=False（API 呼ばない）"""
        mock_validate.return_value = ["too long", "too many lines"]
        with patch("twitter.x_api.requests.post") as mock_post:
            result = post_tweet("dummy")
            self.assertFalse(result["ok"])
            self.assertIn("too long", result["error_body"])
            mock_post.assert_not_called()

    @patch.dict("os.environ", {
        "X_API_KEY": "",
        "X_API_SECRET": "",
        "X_ACCESS_TOKEN": "",
        "X_ACCESS_TOKEN_SECRET": "",
        "X_API_ALLOW_TEST_CALLS": "1",
    }, clear=False)
    @patch("twitter.x_api.validate_tweet_text", return_value=[])
    def test_missing_credentials_returns_failure(self, _mock_validate):
        """credential 不足で ok=False"""
        result = post_tweet("hello")
        self.assertFalse(result["ok"])
        self.assertIn("credentials", result["error_body"])


class PostTweetSuccessPathTest(TestCase):
    """post_tweet の成功パス"""

    @patch.dict("os.environ", VALID_CREDS_ENV, clear=False)
    @patch("twitter.x_api.validate_tweet_text", return_value=[])
    @patch("twitter.x_api.requests.post")
    def test_post_tweet_success_returns_data(self, mock_post, _mock_validate):
        """正常系: data.id を含む応答で ok=True"""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"data": {"id": "999", "text": "hello"}}
        mock_post.return_value = mock_response

        result = post_tweet("hello")
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["id"], "999")

    @patch.dict("os.environ", VALID_CREDS_ENV, clear=False)
    @patch("twitter.x_api.validate_tweet_text", return_value=[])
    @patch("twitter.x_api.requests.post")
    def test_post_tweet_includes_media_ids_in_payload(self, mock_post, _mock_validate):
        """media_ids 指定時に payload に media.media_ids が入る"""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"data": {"id": "111"}}
        mock_post.return_value = mock_response

        post_tweet("hello", media_ids=["m1", "m2"])
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["text"], "hello")
        self.assertEqual(payload["media"]["media_ids"], ["m1", "m2"])


class PostTweetErrorHandlingTest(TestCase):
    """post_tweet の RequestException / HTTP エラー"""

    @patch.dict("os.environ", VALID_CREDS_ENV, clear=False)
    @patch("twitter.x_api.validate_tweet_text", return_value=[])
    @patch("twitter.x_api.requests.post")
    def test_request_exception_returns_failure_with_message(self, mock_post, _mock_validate):
        """ネットワーク例外で ok=False、error_body に例外メッセージ"""
        mock_post.side_effect = requests.RequestException("timeout")
        result = post_tweet("hello")
        self.assertFalse(result["ok"])
        self.assertIn("timeout", result["error_body"])
        self.assertIsNone(result["status_code"])

    @patch.dict("os.environ", VALID_CREDS_ENV, clear=False)
    @patch("twitter.x_api.validate_tweet_text", return_value=[])
    @patch("twitter.x_api.requests.post")
    def test_http_error_extracts_status_code_and_body(self, mock_post, _mock_validate):
        """HTTPError は response.status_code と body を抽出"""
        err_response = MagicMock()
        err_response.status_code = 429
        err_response.text = '{"errors":[{"code":88,"message":"Rate limit"}]}'
        http_error = requests.HTTPError(response=err_response)
        mock_post.side_effect = http_error

        result = post_tweet("hello")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status_code"], 429)
        self.assertIn("Rate limit", result["error_body"])
