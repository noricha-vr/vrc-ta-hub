"""LLM呼び出し時のDB接続管理テスト。"""

import datetime
from django.db import transaction
from unittest.mock import MagicMock, patch

from django.test import TransactionTestCase, tag

from community.models import Community

@tag('offline_external_api')
class LLMConnectionManagementTest(TransactionTestCase):
    """LLM 呼び出し時の DB 接続管理テスト"""

    @patch.dict(
        "os.environ",
        {"OPENROUTER_API_KEY": "test-key", "GEMINI_MODEL": "google/test:free"},
    )
    @patch("twitter.tweet_generator.OpenAI")
    @patch("twitter.tweet_generator.connections.close_all")
    def test_call_llm_closes_db_connections_before_api_request(self, mock_close_all, mock_openai):
        """OpenRouter API 待ちの前にDB接続を解放する"""
        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "生成テキスト"
        mock_client.chat.completions.create.return_value.choices = [mock_choice]
        mock_openai.return_value = mock_client

        from twitter.tweet_generator import _call_llm
        result = _call_llm("system", "user")

        self.assertEqual(result, "生成テキスト")
        mock_close_all.assert_called_once()
        mock_client.chat.completions.create.assert_called_once()

    @patch.dict(
        "os.environ",
        {"OPENROUTER_API_KEY": "test-key", "GEMINI_MODEL": "google/test:free"},
    )
    @patch("twitter.tweet_generator.OpenAI")
    @patch("twitter.tweet_generator.connections.close_all")
    def test_call_llm_keeps_db_connection_inside_atomic(self, mock_close_all, mock_openai):
        """transaction.atomic 内ではDB接続を閉じず、後続の保存を壊さない"""
        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "生成テキスト"
        mock_client.chat.completions.create.return_value.choices = [mock_choice]
        mock_openai.return_value = mock_client

        community = Community.objects.create(
            name="Atomic LLM Test Community",
            start_time=datetime.time(21, 0),
            duration=60,
            weekdays=["Mon"],
            frequency="毎週",
            organizers="Test",
            description="before",
            platform="All",
            status="pending",
        )

        from twitter.tweet_generator import _call_llm
        with transaction.atomic():
            result = _call_llm("system", "user")
            community.description = "after"
            community.save(update_fields=["description"])

        self.assertEqual(result, "生成テキスト")
        mock_close_all.assert_not_called()
        community.refresh_from_db()
        self.assertEqual(community.description, "after")
