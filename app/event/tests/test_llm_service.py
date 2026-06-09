from datetime import date
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from event.llm_service import (
    GeminiEventDateLlmService,
    GeminiProviderConfig,
    OpenAICompatibleEventDateLlmService,
    OpenAICompatibleProviderConfig,
    _normalize_gemini_model_name,
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


class FactoryEdgeCaseTest(SimpleTestCase):
    """get_event_date_llm_service の境界ケース"""

    @override_settings(RECURRENCE_LLM_PROVIDER="UNKNOWN_PROVIDER")
    def test_unknown_provider_raises_value_error(self):
        """未対応プロバイダ名で ValueError（provider 名は lower される）"""
        with self.assertRaises(ValueError) as ctx:
            get_event_date_llm_service()
        self.assertIn("unknown_provider", str(ctx.exception))

    @override_settings(
        RECURRENCE_LLM_PROVIDER="  OpenRouter  ",  # 前後空白 + 大文字混在
        RECURRENCE_LLM_MODEL="x/y",
    )
    def test_provider_name_is_stripped_and_lowercased(self):
        """provider 名は strip + lower される（設定ミス耐性）"""
        service = get_event_date_llm_service()
        self.assertIsInstance(service, OpenAICompatibleEventDateLlmService)
        self.assertEqual(service.config.provider, "openrouter")

    @override_settings(
        RECURRENCE_LLM_PROVIDER="google",  # gemini エイリアス
        RECURRENCE_LLM_MODEL="gemini-1.5-flash",
    )
    def test_google_alias_resolves_to_gemini(self):
        """provider='google' は GeminiEventDateLlmService を返す（エイリアス）"""
        service = get_event_date_llm_service()
        self.assertIsInstance(service, GeminiEventDateLlmService)


class ExtractEventDatesEdgeCaseTest(SimpleTestCase):
    """extract_event_dates の壊れやすいパス"""

    def test_returns_empty_when_no_brackets(self):
        """JSON list 区切りが無いテキストで [] を返す"""
        self.assertEqual(extract_event_dates("just plain text"), [])

    def test_returns_empty_when_malformed_json(self):
        """壊れた JSON で [] を返す（silent recover）"""
        # クォートが閉じていない壊れた JSON list
        text = '["2024-01-01", "broken-quote]'
        self.assertEqual(extract_event_dates(text), [])

    def test_returns_empty_when_parsed_value_is_not_list(self):
        """[] で囲まれていても JSON が dict などの非リストなら [] を返す"""
        # 注意: 現実装は text.find("[")〜rfind("]") を slice するので、内側の list を
        # たまたま拾うこともある。ここでは明示的に非リストにパースされる入力を渡す。
        text = '{"k": "v"} [{"nested": true}]'
        # find("[") は "[" を拾うが、json.loads("[{...}]") は list を返すため空配列
        self.assertEqual(extract_event_dates(text), [])

    def test_skips_non_string_values(self):
        """list 内の非 string 要素 (int, None) は skip して string のみ拾う"""
        text = '["2024-01-01", 42, null, "2024-02-15"]'
        self.assertEqual(
            extract_event_dates(text),
            [date(2024, 1, 1), date(2024, 2, 15)],
        )

    def test_skips_invalid_date_strings(self):
        """日付フォーマット不一致 (YYYY/MM/DD など) を skip"""
        text = '["2024-01-01", "2024/02/15", "not-a-date", "2024-03-10"]'
        self.assertEqual(
            extract_event_dates(text),
            [date(2024, 1, 1), date(2024, 3, 10)],
        )

    def test_dedupes_and_sorts(self):
        """重複除去 + 昇順ソート"""
        text = '["2024-03-10", "2024-01-01", "2024-03-10", "2024-02-15"]'
        self.assertEqual(
            extract_event_dates(text),
            [date(2024, 1, 1), date(2024, 2, 15), date(2024, 3, 10)],
        )

    def test_extracts_only_first_to_last_bracket(self):
        """先頭 [ から末尾 ] までを 1 つの JSON として扱う"""
        text = 'noise [1, 2] middle ["2024-01-01"] tail'
        # find("[") は最初の [、rfind("]") は最後の ]
        # → "[1, 2] middle [\"2024-01-01\"]" → JSON 不正で []
        self.assertEqual(extract_event_dates(text), [])


class OpenAICompatibleApiKeyMissingTest(SimpleTestCase):
    """OpenAICompatibleEventDateLlmService の credential 不足"""

    @patch.dict("os.environ", {"OPENROUTER_API_KEY": ""}, clear=False)
    def test_raises_value_error_when_api_key_missing(self):
        """api_key_env の環境変数が空なら ValueError"""
        config = OpenAICompatibleProviderConfig(
            provider="openrouter",
            api_key_env="OPENROUTER_API_KEY_DOES_NOT_EXIST",
            model_name="x/y",
        )
        service = OpenAICompatibleEventDateLlmService(config)
        with self.assertRaises(ValueError) as ctx:
            service.generate_event_dates("prompt")
        self.assertIn("OPENROUTER_API_KEY_DOES_NOT_EXIST", str(ctx.exception))

    @patch.dict("os.environ", {"FAKE_KEY": "secret"}, clear=False)
    @patch("event.llm_service.OpenAI")
    def test_passes_base_url_when_provided(self, mock_openai_class):
        """base_url 指定時に OpenAI クライアントへ渡される"""
        mock_client = MagicMock()
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock(message=MagicMock(content='["2024-01-01"]'))]
        mock_client.chat.completions.create.return_value = mock_completion
        mock_openai_class.return_value = mock_client

        config = OpenAICompatibleProviderConfig(
            provider="openrouter",
            api_key_env="FAKE_KEY",
            base_url="https://custom.example.com/v1",
            model_name="x/y",
        )
        service = OpenAICompatibleEventDateLlmService(config)
        result = service.generate_event_dates("prompt")

        self.assertEqual(result, [date(2024, 1, 1)])
        call_kwargs = mock_openai_class.call_args.kwargs
        self.assertEqual(call_kwargs["base_url"], "https://custom.example.com/v1")
        self.assertEqual(call_kwargs["api_key"], "secret")


class GeminiApiKeyResolutionTest(SimpleTestCase):
    """GeminiEventDateLlmService の api_key 取得（環境変数 → settings fallback）"""

    @patch.dict("os.environ", {}, clear=True)
    @override_settings(GOOGLE_API_KEY_TEST_FALLBACK="from-settings")
    def test_falls_back_to_settings_when_env_missing(self):
        """環境変数に無ければ settings から取得"""
        # genai を import 時にモック
        with patch.dict("sys.modules", {"google.generativeai": MagicMock()}) as patched_modules:
            mock_genai = patched_modules["google.generativeai"]
            mock_model = MagicMock()
            mock_response = MagicMock(text='["2024-04-01"]')
            mock_model.generate_content.return_value = mock_response
            mock_genai.GenerativeModel.return_value = mock_model

            config = GeminiProviderConfig(
                api_key_env="GOOGLE_API_KEY_TEST_FALLBACK",
                model_name="gemini-1.5-flash",
            )
            service = GeminiEventDateLlmService(config)
            result = service.generate_event_dates("prompt")

        self.assertEqual(result, [date(2024, 4, 1)])
        mock_genai.configure.assert_called_once_with(api_key="from-settings")

    @patch.dict("os.environ", {}, clear=True)
    def test_raises_value_error_when_neither_env_nor_settings_have_key(self):
        """環境変数にも settings にも無ければ ValueError"""
        config = GeminiProviderConfig(
            api_key_env="GEMINI_API_KEY_NOT_DEFINED_ANYWHERE",
            model_name="gemini-1.5-flash",
        )
        service = GeminiEventDateLlmService(config)
        with self.assertRaises(ValueError) as ctx:
            service.generate_event_dates("prompt")
        self.assertIn("GEMINI_API_KEY_NOT_DEFINED_ANYWHERE", str(ctx.exception))


class NormalizeGeminiModelNameTest(SimpleTestCase):
    """_normalize_gemini_model_name の prefix / suffix 除去"""

    def test_strips_google_prefix(self):
        """google/ プレフィックスを除去"""
        self.assertEqual(_normalize_gemini_model_name("google/gemini-1.5-flash"), "gemini-1.5-flash")

    def test_strips_colon_suffix(self):
        """:suffix （free など）を除去"""
        self.assertEqual(_normalize_gemini_model_name("gemini-1.5-flash:free"), "gemini-1.5-flash")

    def test_strips_both_prefix_and_suffix(self):
        """両方除去"""
        self.assertEqual(_normalize_gemini_model_name("google/gemini-1.5-flash:free"), "gemini-1.5-flash")

    def test_returns_unchanged_when_no_prefix_or_suffix(self):
        """素のモデル名はそのまま返す"""
        self.assertEqual(_normalize_gemini_model_name("gemini-1.5-flash"), "gemini-1.5-flash")
