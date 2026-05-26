from datetime import date

from django.test import SimpleTestCase, override_settings

from event.llm_service import (
    GeminiEventDateLlmService,
    OpenAICompatibleEventDateLlmService,
    extract_event_dates,
    get_event_date_llm_service,
)


class EventDateLlmServiceTest(SimpleTestCase):
    def test_extract_event_dates_from_llm_text(self):
        text = """
        以下が生成した日付です。
        ["2024-12-23", "invalid", "2025-01-27", "2024-12-23"]
        """

        self.assertEqual(
            extract_event_dates(text),
            [
                date(2024, 12, 23),
                date(2025, 1, 27),
            ],
        )

    @override_settings(
        RECURRENCE_LLM_PROVIDER="openrouter",
        RECURRENCE_LLM_MODEL="google/test-model",
    )
    def test_factory_uses_openrouter_provider(self):
        service = get_event_date_llm_service()

        self.assertIsInstance(service, OpenAICompatibleEventDateLlmService)
        self.assertEqual(service.config.provider, "openrouter")
        self.assertEqual(service.config.api_key_env, "OPENROUTER_API_KEY")
        self.assertEqual(service.config.model_name, "google/test-model")

    @override_settings(
        RECURRENCE_LLM_PROVIDER="openrouter",
        RECURRENCE_LLM_MODEL="google/test-model",
    )
    def test_openrouter_uses_website_constants(self):
        """OpenRouter 設定が website.constants から組み立てられることを保証する。

        OPENROUTER_BASE_URL と HTTP-Referer がハードコードされた値ではなく、
        website.constants 経由（OPENROUTER_HTTP_REFERER / 環境変数）になっている
        ことを確認する。preview/本番で SITE_URL を切り替えたときに壊れていないか拾う。
        """
        from website.constants import OPENROUTER_BASE_URL, build_openrouter_extra_headers
        from event.llm_service import OPENROUTER_EXTRA_HEADERS

        service = get_event_date_llm_service()

        self.assertEqual(service.config.base_url, OPENROUTER_BASE_URL)
        self.assertEqual(OPENROUTER_EXTRA_HEADERS, build_openrouter_extra_headers())
        self.assertEqual(service.config.extra_headers, build_openrouter_extra_headers())

    @override_settings(
        RECURRENCE_LLM_PROVIDER="openai",
        RECURRENCE_LLM_MODEL="gpt-test-model",
    )
    def test_factory_uses_openai_provider(self):
        service = get_event_date_llm_service()

        self.assertIsInstance(service, OpenAICompatibleEventDateLlmService)
        self.assertEqual(service.config.provider, "openai")
        self.assertEqual(service.config.api_key_env, "OPENAI_API_KEY")
        self.assertEqual(service.config.model_name, "gpt-test-model")

    @override_settings(
        RECURRENCE_LLM_PROVIDER="gemini",
        RECURRENCE_LLM_MODEL="gemini-test-model",
    )
    def test_factory_uses_gemini_provider(self):
        service = get_event_date_llm_service()

        self.assertIsInstance(service, GeminiEventDateLlmService)
        self.assertEqual(service.config.api_key_env, "GOOGLE_API_KEY")
        self.assertEqual(service.config.model_name, "gemini-test-model")
