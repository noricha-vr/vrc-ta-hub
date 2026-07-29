"""投稿文生成プロンプトと失敗処理のテスト。"""

import datetime
from unittest.mock import patch

from django.test import tag

from event.models import EventDetail

from twitter.tests._auto_tweet_test_base import TweetGeneratorTestBase

@tag('offline_external_api')
class TweetGeneratorPromptTest(TweetGeneratorTestBase):
    """投稿文生成プロンプトと失敗処理を検証する。"""

    @patch("twitter.utils._call_llm")
    def test_generate_tweet_uses_post_terminology_in_prompt(self, mock_llm):
        """テンプレートベース生成のプロンプトがX/ポスト表記に統一されている"""
        mock_llm.return_value = "テンプレートベースのポスト"

        from twitter.utils import generate_tweet
        result = generate_tweet("過去の投稿サンプル", {
            "event_name": "Generator Test Community",
            "date": "2026年4月13日(月)",
            "time": "22:00",
            "group_url": "https://vrc.group/TEST.1234",
            "hashtag": "#TestMeetup",
            "details": "22:00 - テストテーマ (テスト太郎)",
        })

        self.assertEqual(result, "テンプレートベースのポスト")
        system_prompt, user_prompt = mock_llm.call_args[0]
        self.assertIn("ポスト", system_prompt)
        self.assertNotIn("ツイート", system_prompt)
        self.assertIn("過去のポスト", user_prompt)
        self.assertIn("告知ポスト", user_prompt)
        self.assertIn("https://vrc.group/TEST.1234", user_prompt)
        self.assertIn("#TestMeetup", user_prompt)
        self.assertNotIn("告知ツイート", user_prompt)

    @patch("twitter.tweet_generator._call_llm")
    def test_generate_tweet_llm_failure(self, mock_llm):
        """LLM 呼び出し失敗時は None を返す"""
        mock_llm.return_value = None

        from twitter.tweet_generator import generate_new_community_tweet
        result = generate_new_community_tweet(self.community)

        self.assertIsNone(result)

    @patch("twitter.services.tweet_generation.threading.Thread")
    @patch("twitter.tweet_generator._call_llm")
    def test_sanitize_strips_newlines_in_prompt(self, mock_llm, _mock_thread):
        """ユーザー入力の改行・制御文字がサニタイズされてプロンプトに渡される"""
        mock_llm.return_value = "サニタイズテスト"

        detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト\n太郎\r\n",
            theme="改行\n入り\tテーマ",
            start_time=datetime.time(22, 15),
        )

        from twitter.tweet_generator import generate_lt_tweet
        generate_lt_tweet(detail)

        call_args = mock_llm.call_args
        user_prompt = call_args[0][1]
        # サニタイズ後は改行が空白に変換されている
        self.assertIn("テスト 太郎", user_prompt)
        self.assertIn("改行 入り テーマ", user_prompt)
