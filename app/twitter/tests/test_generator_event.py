"""発表と当日リマインドの投稿文生成テスト。"""

import datetime
from unittest.mock import patch

from django.test import tag
from django.utils import timezone

from tests.factories import make_event, make_event_detail, make_user
from twitter.models import TweetQueue

from twitter.tests._auto_tweet_test_base import TweetGeneratorTestBase

@tag('offline_external_api')
class TweetGeneratorEventTest(TweetGeneratorTestBase):
    """発表と当日リマインドの告知文生成を検証する。"""

    @patch("twitter.tweet_generator._call_llm")
    def test_generate_lt_tweet(self, mock_llm):
        """LT 告知文が生成される"""
        mock_llm.return_value = "LT告知テスト"

        detail = make_event_detail(
            self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="Pythonのテスト技法",
            start_time=datetime.time(22, 15),
        )

        from twitter.tweet_generator import generate_lt_tweet
        result = generate_lt_tweet(detail)

        self.assertEqual(result, "LT告知テスト")
        call_args = mock_llm.call_args
        system_prompt, user_prompt = call_args[0]
        self.assertIn("告知ポスト", system_prompt)
        self.assertNotIn("告知ツイート", system_prompt)
        self.assertIn("テスト太郎", user_prompt)
        self.assertIn("Pythonのテスト技法", user_prompt)
        self.assertIn("5/1(金)", user_prompt)
        self.assertIn("告知ポスト", user_prompt)
        self.assertNotIn("告知ツイート", user_prompt)

    @patch("twitter.tweet_generator._call_llm")
    def test_generate_lt_tweet_includes_applicant_x_account(self, mock_llm):
        """LT 告知のプロンプトに申請者の X アカウントを含める"""
        mock_llm.return_value = "LT告知テスト"
        applicant = make_user(
            user_name="lt_speaker",
            email="lt_speaker@example.com",
            password="testpassword",
            x_account="speaker_vr",
        )
        detail = make_event_detail(
            self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="Pythonのテスト技法",
            start_time=datetime.time(22, 15),
            applicant=applicant,
        )

        from twitter.tweet_generator import generate_lt_tweet
        generate_lt_tweet(detail)

        _, user_prompt = mock_llm.call_args[0]
        self.assertIn("発表者: テスト太郎さん（@speaker_vr）", user_prompt)
        self.assertIn("発表者（「テスト太郎さん（@speaker_vr）」をそのまま記載）", user_prompt)

    @patch("twitter.tweet_generator._call_llm")
    def test_generate_lt_tweet_uses_name_only_without_applicant_x_account(self, mock_llm):
        """申請者や X アカウントがない LT 告知は従来どおり名前だけを使う"""
        mock_llm.return_value = "LT告知テスト"
        detail = make_event_detail(
            self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="Pythonのテスト技法",
            start_time=datetime.time(22, 15),
        )

        from twitter.tweet_generator import generate_lt_tweet
        generate_lt_tweet(detail)

        _, user_prompt = mock_llm.call_args[0]
        self.assertIn("発表者: テスト太郎さん", user_prompt)
        self.assertNotIn("@", user_prompt)

    @patch("twitter.tweet_generator._call_llm")
    def test_lt_generator_fallback_includes_applicant_x_account(self, mock_llm):
        """LLM 生成失敗時の LT fallback に申請者の X アカウントを含める"""
        mock_llm.return_value = "\n".join(["長い本文"] * 20)
        applicant = make_user(
            user_name="fallback_speaker",
            email="fallback_speaker@example.com",
            password="testpassword",
            x_account="fallback_vr",
        )
        detail = make_event_detail(
            self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="Pythonのテスト技法",
            start_time=datetime.time(22, 15),
            applicant=applicant,
        )
        queue = TweetQueue.objects.create(
            tweet_type="lt",
            community=self.community,
            event=self.event,
            event_detail=detail,
            status="generating",
        )

        from twitter.tweet_generator import get_generator
        result = get_generator("lt")(queue)

        self.assertIn("テスト太郎さん（@fallback_vr）", result)

    @patch("twitter.tweet_generator._call_llm")
    def test_lt_generator_fallback_fits_long_theme_under_weighted_limit(self, mock_llm):
        """LLM が長い本文を返し続けても LT 告知を決定的に280以内へ収める"""
        mock_llm.return_value = (
            "5/16(土) 22:00~ 「計算と自然」集会\n\n"
            "nconcさん「続々 Claw Codeを参考にした、Mathematica-Claude Codeブリッジへの反復ループ型エージェント機能の追加について」\n"
            "自律型エージェントの実装で計算体験がどう変わるかぜひ会場で確かめてください\n\n"
            "詳細はこちら https://vrc-ta-hub.com/community/71/\n"
            "#計算と自然\n"
            "#VRChat技術学術"
        )
        self.community.name = "「計算と自然」集会"
        self.community.twitter_hashtag = "計算と自然"
        self.community.save(update_fields=["name", "twitter_hashtag"])
        self.event.date = datetime.date(2026, 5, 16)
        self.event.start_time = datetime.time(22, 0)
        self.event.save(update_fields=["date", "start_time"])
        detail = make_event_detail(
            self.event,
            detail_type="LT",
            status="approved",
            speaker="nconc",
            theme="続々 Claw Codeを参考にした、Mathematica-Claude Codeブリッジへの反復ループ型エージェント機能の追加について",
            start_time=datetime.time(22, 15),
        )
        queue = TweetQueue.objects.create(
            tweet_type="lt",
            community=self.community,
            event=self.event,
            event_detail=detail,
            status="generating",
        )

        from twitter.tweet_generator import count_tweet_length, get_generator, is_tweet_text_valid
        result = get_generator("lt")(queue)

        self.assertTrue(is_tweet_text_valid(result))
        self.assertLessEqual(count_tweet_length(result), 280)
        self.assertNotIn("自律型エージェントの実装", result)

    @patch("twitter.tweet_generator._call_llm")
    def test_generate_special_event_tweet(self, mock_llm):
        """特別回告知文が生成される"""
        mock_llm.return_value = "特別回告知テスト"

        detail = make_event_detail(
            self.event,
            detail_type="SPECIAL",
            status="approved",
            speaker="ゲスト講師",
            theme="VRの未来",
            start_time=datetime.time(22, 0),
        )

        from twitter.tweet_generator import generate_special_event_tweet
        result = generate_special_event_tweet(detail)

        self.assertEqual(result, "特別回告知テスト")
        call_args = mock_llm.call_args
        system_prompt, user_prompt = call_args[0]
        self.assertIn("告知ポスト", system_prompt)
        self.assertNotIn("告知ツイート", system_prompt)
        self.assertIn("ゲスト講師", user_prompt)
        self.assertIn("5/1(金)", user_prompt)
        self.assertIn("告知ポスト", user_prompt)
        self.assertNotIn("告知ツイート", user_prompt)

    @patch("twitter.tweet_generator._call_llm")
    def test_generate_daily_reminder_tweet(self, mock_llm):
        """当日リマインド生成時に開催情報と発表情報をプロンプトへ含める"""
        mock_llm.return_value = "今日開催のリマインド"

        today_event = make_event(
            self.community,
            event_date=timezone.localdate(),
            start_time=datetime.time(20, 0),
            duration=60,
            weekday="",
            accepts_lt_application=True,
        )
        make_event_detail(
            today_event,
            detail_type="LT",
            status="approved",
            speaker="リマインド太郎",
            theme="今日の見どころ",
            start_time=datetime.time(20, 15),
        )

        from twitter.tweet_generator import generate_daily_reminder_tweet
        result = generate_daily_reminder_tweet(today_event)

        self.assertEqual(result, "今日開催のリマインド")
        system_prompt, user_prompt = mock_llm.call_args[0]
        self.assertIn("リマインダーポスト", system_prompt)
        self.assertNotIn("リマインダーツイート", system_prompt)
        self.assertIn("今日", user_prompt)
        self.assertIn("登録発表数: 1件", user_prompt)
        self.assertIn("リマインド太郎", user_prompt)
        self.assertIn("今日の見どころ", user_prompt)
        self.assertIn("リマインダーポスト", user_prompt)
        self.assertNotIn("リマインダーツイート", user_prompt)

    @patch("twitter.tweet_generator._call_llm")
    def test_generate_daily_reminder_tweet_includes_applicant_x_account(self, mock_llm):
        """当日リマインドの発表一覧に申請者の X アカウントを含める"""
        mock_llm.return_value = "今日開催のリマインド"
        applicant = make_user(
            user_name="reminder_speaker",
            email="reminder_speaker@example.com",
            password="testpassword",
            x_account="reminder_vr",
        )
        today_event = make_event(
            self.community,
            event_date=timezone.localdate(),
            start_time=datetime.time(20, 0),
            duration=60,
            weekday="",
            accepts_lt_application=True,
        )
        make_event_detail(
            today_event,
            detail_type="LT",
            status="approved",
            speaker="リマインド太郎",
            theme="今日の見どころ",
            start_time=datetime.time(20, 15),
            applicant=applicant,
        )

        from twitter.tweet_generator import generate_daily_reminder_tweet
        generate_daily_reminder_tweet(today_event)

        _, user_prompt = mock_llm.call_args[0]
        self.assertIn("- 発表: リマインド太郎さん（@reminder_vr）「今日の見どころ」", user_prompt)

    def test_generate_daily_reminder_tweet_returns_none_without_approved_details(self):
        """approved な LT/SPECIAL がない場合は daily reminder を生成しない"""
        today_event = make_event(
            self.community,
            event_date=timezone.localdate(),
            start_time=datetime.time(20, 0),
            duration=60,
            weekday="",
            accepts_lt_application=True,
        )
        make_event_detail(
            today_event,
            detail_type="BLOG",
            status="approved",
            speaker="ブロガー",
            theme="記事紹介",
            start_time=datetime.time(20, 15),
        )

        from twitter.tweet_generator import generate_daily_reminder_tweet
        result = generate_daily_reminder_tweet(today_event)

        self.assertIsNone(result)
