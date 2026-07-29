"""投稿本文バリデーションのテスト。"""

from unittest.mock import patch

from django.test import TestCase, tag


@tag('offline_external_api')
class PostTweetValidationTest(TestCase):
    """post_tweet 関数の入力バリデーションテスト"""

    def test_empty_text_returns_failure(self):
        """空文字列で ok=False を返す"""
        from twitter.x_api import post_tweet
        result = post_tweet("")
        self.assertFalse(result["ok"])
        self.assertIsNone(result["data"])

    def test_none_text_returns_failure(self):
        """None で ok=False を返す"""
        from twitter.x_api import post_tweet
        result = post_tweet(None)
        self.assertFalse(result["ok"])

    def test_exceeds_280_chars_returns_failure(self):
        """280文字超で ok=False を返す"""
        from twitter.x_api import post_tweet
        long_text = "a" * 281
        result = post_tweet(long_text)
        self.assertFalse(result["ok"])

    def test_exactly_280_chars_does_not_reject(self):
        """280文字ちょうどはバリデーションを通過する（認証情報なしで ok=False になる）"""
        from twitter.x_api import post_tweet
        with patch.dict("os.environ", {
            "X_API_KEY": "",
            "X_API_SECRET": "",
            "X_ACCESS_TOKEN": "",
            "X_ACCESS_TOKEN_SECRET": "",
        }):
            result = post_tweet("a" * 280)
        # 認証情報がないので ok=False だが、文字数バリデーションは通過している
        self.assertFalse(result["ok"])
        # 文字数超過のエラーメッセージではないことを確認
        self.assertNotIn("too long", result["error_body"] or "")
