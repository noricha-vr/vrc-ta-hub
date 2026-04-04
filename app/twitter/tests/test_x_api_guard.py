"""X API テスト環境ガードのテスト

settings.TESTING=True の場合、post_tweet / upload_media が
実際の X API を呼ばずに None を返すことを確認する。
"""

from django.test import TestCase, override_settings

from twitter.x_api import post_tweet, upload_media


@override_settings(TESTING=True)
class XApiTestGuardTest(TestCase):
    """テスト環境での X API ガードテスト"""

    def test_post_tweet_blocked_in_test_environment(self):
        """TESTING=True の場合、post_tweet は None を返す"""
        result = post_tweet("テスト投稿")
        self.assertIsNone(result)

    def test_upload_media_blocked_in_test_environment(self):
        """TESTING=True の場合、upload_media は None を返す"""
        result = upload_media("https://data.vrc-ta-hub.com/test.png")
        self.assertIsNone(result)
