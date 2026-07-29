"""新規集会の投稿文生成テスト。"""

from unittest.mock import patch

from django.test import tag


from twitter.tests._auto_tweet_test_base import TweetGeneratorTestBase

@tag('offline_external_api')
class TweetGeneratorCommunityTest(TweetGeneratorTestBase):
    """新規集会の告知文生成を検証する。"""

    @patch("twitter.tweet_generator._call_llm")
    def test_generate_new_community_tweet(self, mock_llm):
        """新規集会の告知文が生成��れる"""
        mock_llm.return_value = "新しい集会がはじまります！"

        from twitter.tweet_generator import generate_new_community_tweet
        result = generate_new_community_tweet(self.community, self.event)

        self.assertEqual(result, "新しい集会がはじまります！")
        mock_llm.assert_called_once()

        # プロンプトに集会名が含まれていることを確認
        call_args = mock_llm.call_args
        system_prompt, user_prompt = call_args[0]
        self.assertIn("ポスト", system_prompt)
        self.assertNotIn("ツイート", system_prompt)
        self.assertIn("Generator Test Community", call_args[0][1])
        self.assertIn("5/1(金)", user_prompt)
        self.assertIn("告知ポスト", user_prompt)
        self.assertNotIn("告知ツイート", user_prompt)
