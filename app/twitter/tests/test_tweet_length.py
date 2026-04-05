"""ツイート文字数カウントとリトライ機能のテスト"""

from unittest.mock import patch

from django.test import TestCase

from twitter.tweet_generator import (
    TWEET_MAX_WEIGHTED_LENGTH,
    _generate_with_retry,
    count_tweet_length,
)


class CountTweetLengthTest(TestCase):
    """count_tweet_length のテスト"""

    def test_ascii_only(self):
        """ASCII文字は重み1"""
        self.assertEqual(count_tweet_length("hello"), 5)

    def test_japanese_only(self):
        """日本語文字（U+1100以上）は重み2"""
        self.assertEqual(count_tweet_length("こんにちは"), 10)

    def test_mixed(self):
        """ASCII + 日本語の混在"""
        # "LT" = 2, "告知" = 4, total = 6
        self.assertEqual(count_tweet_length("LT告知"), 6)

    def test_url_counts_as_23(self):
        """URLは実際の長さに関係なく23としてカウント"""
        text = "詳細はこちら https://vrc-ta-hub.com/event/123/"
        # "詳細はこちら " = 6*2+1 = 13, URL = 23, total = 36
        self.assertEqual(count_tweet_length(text), 36)

    def test_multiple_urls(self):
        """複数URLはそれぞれ23としてカウント"""
        text = "https://example.com https://example.org"
        # space = 1, URL*2 = 46, total = 47
        self.assertEqual(count_tweet_length(text), 47)

    def test_empty_string(self):
        self.assertEqual(count_tweet_length(""), 0)

    def test_boundary_u10ff(self):
        """U+10FF (weight 1) の境界テスト"""
        self.assertEqual(count_tweet_length("\u10FF"), 1)

    def test_boundary_u1100(self):
        """U+1100 (weight 2) の境界テスト"""
        self.assertEqual(count_tweet_length("\u1100"), 2)

    def test_realistic_tweet(self):
        """実際のツイートに近いテキストの文字数確認"""
        text = (
            "【LT告知】次回の #個人開発集会 は4/9(木) 22:00から！\n"
            "\n"
            "発表者はネバーさん。テーマは「テスト」です！\n"
            "\n"
            "詳細はこちら https://vrc-ta-hub.com/event/123/\n"
            "#VRChat技術学術"
        )
        length = count_tweet_length(text)
        self.assertLessEqual(length, TWEET_MAX_WEIGHTED_LENGTH)


class GenerateWithRetryTest(TestCase):
    """_generate_with_retry のテスト"""

    def test_returns_on_first_success(self):
        """初回生成が280以内なら即返す"""
        def fake_generator(target_chars=140):
            return "short tweet"

        result = _generate_with_retry(fake_generator)
        self.assertEqual(result, "short tweet")

    def test_retries_on_too_long(self):
        """超過時にtarget_charsを減らしてリトライする"""
        call_count = 0

        def fake_generator(target_chars=140):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                # 最初の2回: 長すぎるテキスト（日本語280文字 ≈ 560 weighted）
                return "あ" * 200
            # 3回目: 短いテキスト
            return "OK" * 10

        result = _generate_with_retry(fake_generator)
        self.assertEqual(result, "OK" * 10)
        self.assertEqual(call_count, 3)

    def test_returns_last_result_after_all_retries(self):
        """全リトライ後も超過なら最後の結果を返す"""
        def fake_generator(target_chars=140):
            return "あ" * 200

        result = _generate_with_retry(fake_generator, max_retries=3)
        self.assertEqual(result, "あ" * 200)

    def test_returns_none_on_generation_failure(self):
        """生成失敗(None)時はリトライせずNoneを返す"""
        def fake_generator(target_chars=140):
            return None

        result = _generate_with_retry(fake_generator)
        self.assertIsNone(result)

    def test_target_chars_decreases_on_retry(self):
        """リトライごとにtarget_charsが20ずつ減少する"""
        targets = []

        def fake_generator(target_chars=140):
            targets.append(target_chars)
            return "あ" * 200  # Always too long

        _generate_with_retry(fake_generator, max_retries=3)
        self.assertEqual(targets, [140, 120, 100, 80])

    def test_with_positional_args(self):
        """位置引数付きの生成関数が正しく呼ばれる"""
        def fake_generator(arg1, arg2, target_chars=140):
            return f"{arg1}-{arg2}-{target_chars}"

        result = _generate_with_retry(fake_generator, "a", "b")
        self.assertEqual(result, "a-b-140")
