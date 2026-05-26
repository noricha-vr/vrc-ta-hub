"""定期イベントの日付生成に使うLLMアダプタ。"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from typing import Protocol

from django.conf import settings
from openai import OpenAI
from pydantic import BaseModel, Field

from website.constants import OPENROUTER_BASE_URL, build_site_url

logger = logging.getLogger(__name__)

DEFAULT_RECURRENCE_LLM_PROVIDER = "openrouter"
DEFAULT_RECURRENCE_MODEL = "google/gemini-2.0-flash-exp"
OPENROUTER_EXTRA_HEADERS = {
    "HTTP-Referer": build_site_url("/"),
    "X-Title": "VRC TA Hub",
}
SYSTEM_PROMPT = "あなたは定期イベントの日付を生成する専門家です。必ず指定されたJSON形式で出力してください。"


class EventDateLlmService(Protocol):
    """定期イベントの日付生成をLLMプロバイダから切り離す。"""

    def generate_event_dates(self, prompt: str) -> list[date]:
        """Generate event dates from the given prompt.

        Args:
            prompt: 日付生成条件を含むユーザープロンプト。

        Returns:
            LLM応答から抽出した日付リスト。
        """


class OpenAICompatibleProviderConfig(BaseModel):
    """OpenAI互換APIへ接続するための設定。"""

    provider: str
    api_key_env: str
    model_name: str
    base_url: str | None = None
    extra_headers: dict[str, str] = Field(default_factory=dict)


class GeminiProviderConfig(BaseModel):
    """Google Gemini APIへ接続するための設定。"""

    api_key_env: str
    model_name: str


class OpenAICompatibleEventDateLlmService:
    """OpenAI SDK互換のChat Completions APIで日付を生成する。"""

    def __init__(self, config: OpenAICompatibleProviderConfig):
        self.config = config

    def generate_event_dates(self, prompt: str) -> list[date]:
        """Generate event dates with an OpenAI-compatible provider.

        Args:
            prompt: 日付生成条件を含むユーザープロンプト。

        Returns:
            LLM応答から抽出した日付リスト。

        Raises:
            ValueError: 必須APIキーが環境変数にない場合。
        """
        api_key = os.environ.get(self.config.api_key_env)
        if not api_key:
            raise ValueError(f"{self.config.api_key_env} is required for {self.config.provider}")

        client_kwargs = {"api_key": api_key}
        if self.config.base_url:
            client_kwargs["base_url"] = self.config.base_url

        client = OpenAI(**client_kwargs)
        completion = client.chat.completions.create(
            extra_headers=self.config.extra_headers,
            model=self.config.model_name,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=2000,
        )
        text = completion.choices[0].message.content or ""
        return extract_event_dates(text)


class GeminiEventDateLlmService:
    """Google Gemini APIで日付を生成する。"""

    def __init__(self, config: GeminiProviderConfig):
        self.config = config

    def generate_event_dates(self, prompt: str) -> list[date]:
        """Generate event dates with Google Gemini.

        Args:
            prompt: 日付生成条件を含むユーザープロンプト。

        Returns:
            LLM応答から抽出した日付リスト。

        Raises:
            ValueError: 必須APIキーが環境変数またはsettingsにない場合。
        """
        api_key = os.environ.get(self.config.api_key_env) or getattr(settings, self.config.api_key_env, None)
        if not api_key:
            raise ValueError(f"{self.config.api_key_env} is required for gemini")

        import google.generativeai as genai

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(_normalize_gemini_model_name(self.config.model_name))
        response = model.generate_content(
            [SYSTEM_PROMPT, prompt],
            generation_config={
                "temperature": 0.3,
                "max_output_tokens": 2000,
            },
        )
        return extract_event_dates(getattr(response, "text", "") or "")


def get_event_date_llm_service() -> EventDateLlmService:
    """Build the configured event-date LLM service.

    Returns:
        settings.RECURRENCE_LLM_PROVIDER に対応する日付生成サービス。

    Raises:
        ValueError: 未対応のプロバイダ名が指定された場合。
    """
    provider = str(
        getattr(settings, "RECURRENCE_LLM_PROVIDER", DEFAULT_RECURRENCE_LLM_PROVIDER)
    ).strip().lower()
    model_name = str(
        getattr(settings, "RECURRENCE_LLM_MODEL", getattr(settings, "GEMINI_MODEL", DEFAULT_RECURRENCE_MODEL))
    )

    if provider == "openrouter":
        return OpenAICompatibleEventDateLlmService(
            OpenAICompatibleProviderConfig(
                provider=provider,
                api_key_env=str(getattr(settings, "RECURRENCE_LLM_API_KEY_ENV", "OPENROUTER_API_KEY")),
                base_url=str(getattr(settings, "RECURRENCE_LLM_BASE_URL", OPENROUTER_BASE_URL)),
                model_name=model_name,
                extra_headers=OPENROUTER_EXTRA_HEADERS,
            )
        )

    if provider == "openai":
        base_url = getattr(settings, "RECURRENCE_LLM_BASE_URL", None)
        return OpenAICompatibleEventDateLlmService(
            OpenAICompatibleProviderConfig(
                provider=provider,
                api_key_env=str(getattr(settings, "RECURRENCE_LLM_API_KEY_ENV", "OPENAI_API_KEY")),
                base_url=str(base_url) if base_url else None,
                model_name=model_name,
            )
        )

    if provider in {"gemini", "google"}:
        return GeminiEventDateLlmService(
            GeminiProviderConfig(
                api_key_env=str(getattr(settings, "RECURRENCE_LLM_API_KEY_ENV", "GOOGLE_API_KEY")),
                model_name=model_name,
            )
        )

    raise ValueError(f"Unsupported RECURRENCE_LLM_PROVIDER: {provider}")


def extract_event_dates(text: str) -> list[date]:
    """Extract YYYY-MM-DD date values from a JSON list in LLM text.

    Args:
        text: LLMが返したテキスト。

    Returns:
        パースできた日付の昇順リスト。
    """
    json_start = text.find("[")
    json_end = text.rfind("]") + 1
    if json_start == -1 or json_end <= json_start:
        return []

    try:
        values = json.loads(text[json_start:json_end])
    except json.JSONDecodeError:
        logger.warning("Failed to parse LLM date JSON", exc_info=True)
        return []

    if not isinstance(values, list):
        return []

    dates = []
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            dates.append(datetime.strptime(value, "%Y-%m-%d").date())
        except ValueError:
            continue
    return sorted(set(dates))


def _normalize_gemini_model_name(model_name: str) -> str:
    if model_name.startswith("google/"):
        model_name = model_name.split("/", 1)[1]
    if ":" in model_name:
        model_name = model_name.split(":", 1)[0]
    return model_name
