"""ツイート文字数カウントとリトライ機能のテスト"""


from django.test import TestCase

from twitter.tweet_generator import (
    GROUP_ID_LINE_PREFIX,
    MAX_BODY_LINES,
    PROFILE_LINK_FALLBACK_LINE,
    TWEET_MAX_WEIGHTED_LENGTH,
    _generate_with_retry,
    contains_url,
    count_body_lines,
    count_tweet_length,
    defang_urls_for_x,
    is_tweet_text_valid,
    validate_tweet_text,
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


class CountBodyLinesTest(TestCase):
    """count_body_lines のテスト（X スパムフィルタ回避のための本文行数カウント）"""

    def test_empty_string(self):
        self.assertEqual(count_body_lines(""), 0)

    def test_single_body_line(self):
        self.assertEqual(count_body_lines("今夜 22:00〜 集会"), 1)

    def test_excludes_empty_lines(self):
        text = "1行目\n\n2行目\n\n"
        self.assertEqual(count_body_lines(text), 2)

    def test_excludes_hashtag_lines(self):
        text = "本文\n#VRChat技術学術\n#個人開発集会"
        self.assertEqual(count_body_lines(text), 1)

    def test_excludes_url_lines(self):
        text = "本文\n詳細はこちら https://vrc-ta-hub.com/event/123/"
        # URL行は除外
        self.assertEqual(count_body_lines(text), 1)

    def test_excludes_http_url_lines(self):
        text = "本文\nhttp://example.com"
        self.assertEqual(count_body_lines(text), 1)

    def test_realistic_daily_reminder_3_lines(self):
        """3行以内の典型的な daily_reminder ポスト"""
        text = (
            "今夜 22:00〜 個人開発集会\n"
            "\n"
            "ネバーさん「テスト駆動開発入門」\n"
            "実例を交えたTDDの基礎が聞けます\n"
            "\n"
            "詳細はこちら https://vrc-ta-hub.com/community/1/\n"
            "#個人開発集会\n"
            "#VRChat技術学術"
        )
        self.assertEqual(count_body_lines(text), 3)

    def test_realistic_4_body_lines_triggers_spam_filter(self):
        """本文4行のケース（スパムフィルタ発火条件）を検出"""
        text = (
            "今夜 22:00〜 個人開発集会\n"
            "\n"
            "ネバーさん「テスト駆動開発入門」\n"
            "実例を交えたTDDの基礎が聞けます\n"
            "ぜひ聞きに来てください\n"
            "\n"
            "詳細はこちら https://vrc-ta-hub.com/community/1/\n"
            "#VRChat技術学術"
        )
        self.assertEqual(count_body_lines(text), 4)
        self.assertGreater(count_body_lines(text), MAX_BODY_LINES)

    def test_leading_trailing_whitespace_line_is_empty(self):
        text = "   \n本文"
        self.assertEqual(count_body_lines(text), 1)


class TweetValidationTest(TestCase):
    """投稿前バリデーションのテスト"""

    def test_detects_weighted_length_over_limit_even_if_python_len_is_short(self):
        text = (
            "5/16(土) 22:00~ 「計算と自然」集会\n\n"
            "nconcさん「続々 Claw Codeを参考にした、Mathematica-Claude Codeブリッジへの反復ループ型エージェント機能の追加について」\n"
            "自律型エージェントの実装で計算体験がどう変わるかぜひ会場で確かめてください\n\n"
            "詳細はこちら https://vrc-ta-hub.com/community/71/\n"
            "#計算と自然\n"
            "#VRChat技術学術"
        )

        self.assertLessEqual(len(text), 280)
        self.assertGreater(count_tweet_length(text), TWEET_MAX_WEIGHTED_LENGTH)
        self.assertFalse(is_tweet_text_valid(text))
        self.assertIn("weighted_length=", validate_tweet_text(text)[0])


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
            if call_count < 2:
                # 初回: 長すぎるテキスト（日本語280文字 ≈ 560 weighted）
                return "あ" * 200
            # 2回目: 短いテキスト
            return "OK" * 10

        result = _generate_with_retry(fake_generator)
        self.assertEqual(result, "OK" * 10)
        self.assertEqual(call_count, 2)

    def test_llm_retry_limited_to_one(self):
        """バリデーション違反が続いてもLLM呼び出しは2回で打ち切りfallbackへ切り替える"""
        call_count = 0

        def fake_generator(target_chars=140):
            nonlocal call_count
            call_count += 1
            return "あ" * 200  # 常に長すぎる

        result = _generate_with_retry(fake_generator, fallback_fn=lambda: "短い告知")

        self.assertEqual(call_count, 2)
        self.assertEqual(result, "短い告知")

    def test_returns_none_after_all_retries_without_fallback(self):
        """全リトライ後も超過し、fallback がなければ None を返す"""
        def fake_generator(target_chars=140):
            return "あ" * 200

        result = _generate_with_retry(fake_generator, max_retries=3)
        self.assertIsNone(result)

    def test_uses_fallback_after_all_retries(self):
        """LLM が長文を返し続けたら決定的フォールバックへ切り替える"""
        def fake_generator(target_chars=140):
            return "あ" * 200

        result = _generate_with_retry(
            fake_generator,
            max_retries=1,
            fallback_fn=lambda: "短い告知",
        )

        self.assertEqual(result, "短い告知")

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

    def test_retries_on_too_many_body_lines(self):
        """本文4行以上の場合はリトライする（X スパムフィルタ回避）"""
        call_count = 0

        def fake_generator(target_chars=140):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return (
                    "1行目\n"
                    "2行目\n"
                    "3行目\n"
                    "4行目\n"
                    "\n"
                    "詳細はこちら https://example.com"
                )
            return "短い本文\n\n詳細はこちら https://example.com"

        result = _generate_with_retry(fake_generator)
        self.assertEqual(call_count, 2)
        self.assertLessEqual(count_body_lines(result), MAX_BODY_LINES)

    def test_with_positional_args(self):
        """位置引数付きの生成関数が正しく呼ばれる"""
        def fake_generator(arg1, arg2, target_chars=140):
            return f"{arg1}-{arg2}-{target_chars}"

        result = _generate_with_retry(fake_generator, "a", "b")
        self.assertEqual(result, "a-b-140")

    def test_forbid_urls_triggers_retry_and_fallback(self):
        """forbid_urls=True の時 URL 混入出力を拒否しリトライ→フォールバックする"""
        feedbacks = []

        def fake_generator(target_chars=140, validation_feedback=""):
            feedbacks.append(validation_feedback)
            return "11/25(水) 22:00~ 集会\n\n詳細はこちら https://vrc-ta-hub.com/community/1/"

        result = _generate_with_retry(
            fake_generator,
            max_retries=1,
            forbid_urls=True,
            fallback_fn=lambda: f"11/25(水) 22:00~ 集会\n\n{GROUP_ID_LINE_PREFIX} GAME.2515",
        )
        self.assertEqual(len(feedbacks), 2)
        self.assertIn("URL やドメイン", feedbacks[1])
        self.assertEqual(result, "11/25(水) 22:00~ 集会\n\nVRChatグループID: GAME.2515")


class UrlValidationAndFooterLineTest(TestCase):
    """contains_url とグループID案内行除外のテスト"""

    def test_contains_url_detects_urls_and_bare_domains(self):
        for url_text in (
            "詳細は https://vrc-ta-hub.com/community/1/",
            "http://example.com",
            "参加: vrc.group/GAME.2515",
            "VRC.GROUP/GAME.2515",
            "詳細 vrc-ta-hub.com",
            "EXAMPLE.COM",
            "https://vrchat.com/home/group/grp_1234",
            "x.com/vrc_ta_hub",
            "t.co/abc123",
            "youtu.be/xyz",
            "example.edu",
            "example.uk",
            "example.social",
            "example.store",
            "example.xn--p1ai",
            "example.xn--vermgensberater-ctb",
            "café.com",
            "пример.com",
            "twitter.みんな",
            "example.com.",
            "example.com.invalid",
            "foo_bar.example.com",
            "example.onion",
            "example.vermögensberatung",
            "example.vermo\u0308gensberatung",
            "EXAMPLE.VERMÖGENSBERATUNG",
            "cafe\u0301.com",
            "example.みんな入門",
            "example.कॉम",
            "example.zippo",
            "example.abarth",
            "example.xperia",
            "//example.zippo",
            "example.zippo/path\n次の行",
            "ASP.NET",
            "Socket.IO",
            "discord.py",
        ):
            with self.subTest(url_text=url_text):
                self.assertTrue(contains_url(url_text))

    def test_defang_urls_for_x_preserves_tech_names_and_removes_url_detection(self):
        for raw in (
            "ASP.NET Core 入門",
            "Socket.IO 入門",
            "discord.py 入門",
            "sub.domain.co.uk",
            "example.com.invalid",
            "foo_bar.example.com",
            "example.onion",
            "example.vermögensberatung",
            "example.vermo\u0308gensberatung",
            "EXAMPLE.VERMÖGENSBERATUNG",
            "cafe\u0301.com",
            "example.みんな入門",
            "example.कॉम",
            "https://example.vermögensberatung",
            "example.zippo",
            "example.abarth",
            "example.xperia",
            "https://example.zippo",
            "//example.zippo",
        ):
            with self.subTest(raw=raw):
                defanged = defang_urls_for_x(raw)
                self.assertFalse(contains_url(defanged))
                self.assertIn("・", defanged)
        self.assertEqual(defang_urls_for_x("Three.js入門"), "Three.js入門")
        self.assertEqual(defang_urls_for_x(f"{GROUP_ID_LINE_PREFIX} GAME.2515"), f"{GROUP_ID_LINE_PREFIX} GAME.2515")

    def test_contains_url_allows_group_id_and_tech_names(self):
        for safe_text in (
            f"{GROUP_ID_LINE_PREFIX} GAME.2515",
            "Three.jsとUnityで作るワールド",
            "Node.js入門セッション",
            "UdonSharp.cs で書くギミック",
            "foo_bar.com",
            "example.vermögensberatung2",
            "Python 3.12 の新機能",
            PROFILE_LINK_FALLBACK_LINE,
        ):
            with self.subTest(safe_text=safe_text):
                self.assertFalse(contains_url(safe_text))

    def test_count_body_lines_excludes_group_id_and_profile_fallback_lines(self):
        text = (
            "11/25(水) 22:00~ テスト集会\n\n"
            "太郎さん「VRChat入門」\n"
            "基礎から丁寧に解説します\n\n"
            f"{GROUP_ID_LINE_PREFIX} GAME.2515\n"
            "#VRChat"
        )
        self.assertEqual(count_body_lines(text), 3)
        fallback_text = text.replace(f"{GROUP_ID_LINE_PREFIX} GAME.2515", PROFILE_LINK_FALLBACK_LINE)
        self.assertEqual(count_body_lines(fallback_text), 3)

    def test_validate_tweet_text_forbid_urls(self):
        text = "11/25(水) 22:00~ テスト集会\n\n太郎さん「VRChat入門」\n\nvrc.group/GAME.2515"
        self.assertIn("contains_url", validate_tweet_text(text, forbid_urls=True))
        self.assertEqual(validate_tweet_text(text, forbid_urls=False), [])
