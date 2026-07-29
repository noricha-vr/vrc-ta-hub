"""資料共有の投稿文生成テスト。"""

import datetime
from unittest.mock import patch

from django.test import tag

from tests.factories import make_event_detail, make_user
from twitter.models import TweetQueue

from twitter.tests._auto_tweet_test_base import TweetGeneratorTestBase

@tag('offline_external_api')
class TweetGeneratorSlideShareTest(TweetGeneratorTestBase):
    """資料共有の告知文生成を検証する。"""

    @patch("twitter.tweet_generator._call_llm")
    def test_generate_slide_share_tweet_with_slide(self, mock_llm):
        """スライド共有ツイートが生成される（slide_url のみ）"""
        mock_llm.return_value = "スライド公開しました！"

        detail = make_event_detail(
            self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="Pythonのテスト技法",
            start_time=datetime.time(22, 15),
            slide_url="https://example.com/slides",
        )

        from twitter.tweet_generator import generate_slide_share_tweet
        result = generate_slide_share_tweet(detail)

        self.assertEqual(result, "スライド公開しました！")
        call_args = mock_llm.call_args
        system_prompt, user_prompt = call_args[0]
        self.assertIn("ポスト", system_prompt)
        self.assertNotIn("ツイート", system_prompt)
        self.assertIn("ポスト", user_prompt)
        self.assertNotIn("ツイート", user_prompt)
        self.assertIn("テスト太郎", user_prompt)
        self.assertIn("Pythonのテスト技法", user_prompt)
        # URLはプロンプトに含めない（プロンプトインジェクション防止）
        self.assertNotIn("https://example.com/slides", user_prompt)
        self.assertIn("スライド", user_prompt)
        self.assertNotIn("動画", user_prompt)

    @patch("twitter.tweet_generator._call_llm")
    def test_generate_slide_share_tweet_with_youtube(self, mock_llm):
        """スライド共有ツイートが生成される（youtube_url のみ）"""
        mock_llm.return_value = "動画公開しました！"

        detail = make_event_detail(
            self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="VR技術入門",
            start_time=datetime.time(22, 15),
            youtube_url="https://youtube.com/watch?v=test123",
        )

        from twitter.tweet_generator import generate_slide_share_tweet
        result = generate_slide_share_tweet(detail)

        self.assertEqual(result, "動画公開しました！")
        call_args = mock_llm.call_args
        system_prompt, user_prompt = call_args[0]
        self.assertIn("ポスト", system_prompt)
        self.assertNotIn("ツイート", system_prompt)
        self.assertIn("ポスト", user_prompt)
        self.assertNotIn("ツイート", user_prompt)
        self.assertNotIn("https://youtube.com", user_prompt)
        self.assertIn("動画", user_prompt)
        self.assertNotIn("スライド", user_prompt)

    @patch("twitter.tweet_generator._call_llm")
    def test_generate_slide_share_tweet_with_both(self, mock_llm):
        """スライド共有ツイートが生成される（slide_url + youtube_url 両方）"""
        mock_llm.return_value = "スライドと動画公開！"

        detail = make_event_detail(
            self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="VR技術入門",
            start_time=datetime.time(22, 15),
            slide_url="https://example.com/slides",
            youtube_url="https://youtube.com/watch?v=test123",
        )

        from twitter.tweet_generator import generate_slide_share_tweet
        result = generate_slide_share_tweet(detail)

        self.assertEqual(result, "スライドと動画公開！")
        call_args = mock_llm.call_args
        system_prompt, user_prompt = call_args[0]
        self.assertIn("ポスト", system_prompt)
        self.assertNotIn("ツイート", system_prompt)
        self.assertIn("ポスト", user_prompt)
        self.assertNotIn("ツイート", user_prompt)
        self.assertIn("スライド・動画", user_prompt)

    @patch("twitter.tweet_generator._call_llm")
    def test_generate_slide_share_tweet_includes_applicant_x_account(self, mock_llm):
        """スライド共有告知のプロンプトに申請者の X アカウントを含める"""
        mock_llm.return_value = "スライド公開テスト"
        applicant = make_user(
            user_name="slide_speaker",
            email="slide_speaker@example.com",
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
            slide_url="https://example.com/slides",
            applicant=applicant,
        )

        from twitter.tweet_generator import generate_slide_share_tweet
        generate_slide_share_tweet(detail)

        _, user_prompt = mock_llm.call_args[0]
        self.assertIn("発表者: テスト太郎さん（@speaker_vr）", user_prompt)
        self.assertIn("発表者（「テスト太郎さん（@speaker_vr）」をそのまま記載）", user_prompt)

    @patch("twitter.tweet_generator._call_llm")
    def test_generate_slide_share_tweet_uses_name_only_without_applicant_x_account(self, mock_llm):
        """申請者や X アカウントがないスライド告知は従来どおり名前だけを使う"""
        mock_llm.return_value = "スライド公開テスト"
        detail = make_event_detail(
            self.event,
            detail_type="LT",
            status="approved",
            speaker="テスト太郎",
            theme="Pythonのテスト技法",
            start_time=datetime.time(22, 15),
            slide_url="https://example.com/slides",
        )

        from twitter.tweet_generator import generate_slide_share_tweet
        generate_slide_share_tweet(detail)

        _, user_prompt = mock_llm.call_args[0]
        self.assertIn("発表者: テスト太郎さん", user_prompt)
        self.assertNotIn("@", user_prompt)

    @patch("twitter.tweet_generator._call_llm")
    def test_slide_share_generator_fallback_includes_applicant_x_account(self, mock_llm):
        """LLM 生成失敗時のスライド共有 fallback に申請者の X アカウントを含める"""
        mock_llm.return_value = "\n".join(["長い本文"] * 20)
        applicant = make_user(
            user_name="slide_fallback_speaker",
            email="slide_fallback_speaker@example.com",
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
            slide_url="https://example.com/slides",
            applicant=applicant,
        )
        queue = TweetQueue.objects.create(
            tweet_type="slide_share",
            community=self.community,
            event=self.event,
            event_detail=detail,
            status="generating",
        )

        from twitter.tweet_generator import get_generator
        result = get_generator("slide_share")(queue)

        self.assertIn("テスト太郎さん（@fallback_vr）", result)
