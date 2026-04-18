"""X API テスト環境ガードのテスト

settings.TESTING=True の場合、post_tweet / upload_media が
実際の X API を呼ばずに失敗結果を返すことを確認する。
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from twitter.x_api import post_tweet, upload_media


@override_settings(TESTING=True)
class XApiTestGuardTest(TestCase):
    """テスト環境での X API ガードテスト"""

    def test_post_tweet_blocked_in_test_environment(self):
        """TESTING=True の場合、post_tweet は ok=False を返す"""
        result = post_tweet("テスト投稿")
        self.assertFalse(result["ok"])
        self.assertIsNone(result["data"])

    def test_upload_media_blocked_in_test_environment(self):
        """TESTING=True の場合、upload_media は None を返す"""
        result = upload_media("https://data.vrc-ta-hub.com/test.png")
        self.assertIsNone(result)


class PostTweetResponseValidationTest(TestCase):
    """post_tweet のレスポンス検証テスト（tweet_id 欠落は失敗扱い）"""

    @patch.dict(
        "os.environ",
        {
            "X_API_KEY": "k",
            "X_API_SECRET": "s",
            "X_ACCESS_TOKEN": "t",
            "X_ACCESS_TOKEN_SECRET": "ts",
            "X_API_ALLOW_TEST_CALLS": "1",
        },
    )
    @patch("twitter.x_api.requests.post")
    def test_post_tweet_returns_failure_when_response_has_no_tweet_id(self, mock_post):
        """HTTP 2xx でも data に id が無い場合は ok=False を返す"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"data": {}}'
        mock_response.json.return_value = {"data": {}}
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        result = post_tweet("テスト投稿")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status_code"], 200)
        self.assertIn("Missing tweet id", result["error_body"])
