"""tweet_generator 互換シム (後方互換性のため re-export)

実装は `twitter.generators.*` サブパッケージへ分割済み:
- common: 共通定数・バリデーション・サニタイズ・整形ユーティリティ
- retry: 文字数バリデーション付きリトライラッパー
- lt_tweet: 発表 (LT) / 特別回告知
- daily_reminder: 当日リマインダー
- event_intro: スライド・記事共有
- community: 新規集会告知

新規コードは `from twitter.generators import ...` を使うこと。
本ファイルは段階的に削除予定だが、以下の理由で当面は残す:

1. `signals.py` / `x_api.py` / `utils.py` など外部参照の後方互換
2. テストの `@patch("twitter.tweet_generator._call_llm")` 等のモジュールパス指定
   - `_call_llm` 本体はこの module 内に置き、各サブモジュールは
     `from twitter import tweet_generator` を遅延 import して
     `tweet_generator._call_llm(...)` 経由で呼ぶことで patch が効く
3. `@patch("twitter.tweet_generator.OpenAI")` / `connections.close_all` も
   `_call_llm` がここに居ることで成立
"""

import logging
import os

from django.conf import settings
from django.db import connections
from openai import OpenAI

from ta_hub.libs import cloudflare_image_url
from twitter.generators.common import (  # noqa: F401
    BODY_LINE_CONSTRAINT,
    LLM_TEMPERATURE,
    MAX_BODY_LINES,
    MAX_TWEET_TOKENS,
    RETRY_TARGET_CHARS_STEP,
    SANITIZE_MAX_LENGTH,
    TRUNCATION_SUFFIX,
    TWEET_MAX_WEIGHTED_LENGTH,
    URL_WEIGHTED_LENGTH,
    WEEKDAY_NAMES,
    _build_hashtag_suffix,
    _build_tweet,
    _fit_candidate,
    _format_speaker_display,
    _format_weekdays,
    _sanitize_for_prompt,
    _trim_to_weight,
    _validation_feedback,
    count_body_lines,
    count_tweet_length,
    is_tweet_text_valid,
    validate_tweet_text,
)
from twitter.generators.community import (  # noqa: F401
    _fallback_new_community_tweet,
    generate_new_community_tweet,
)
from twitter.generators.daily_reminder import (  # noqa: F401
    _fallback_daily_reminder_tweet,
    generate_daily_reminder_tweet,
)
from twitter.generators.event_intro import (  # noqa: F401
    _fallback_slide_share_tweet,
    generate_slide_share_tweet,
)
from twitter.generators.lt_tweet import (  # noqa: F401
    _fallback_presentation_tweet,
    generate_lt_tweet,
    generate_special_event_tweet,
)
from twitter.generators.retry import (  # noqa: F401
    _call_generate_fn,
    _generate_with_retry,
)
from website.constants import (
    OPENROUTER_BASE_URL,
    build_openrouter_extra_headers,
)

logger = logging.getLogger(__name__)


def _call_llm(system_prompt: str, user_prompt: str) -> str | None:
    """OpenRouter API 経由で LLM を呼び出す共通関数。

    Returns:
        生成テキスト。失敗時は None。
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        logger.error("OPENROUTER_API_KEY is not set")
        return None

    model = os.environ.get("GEMINI_MODEL", "google/gemini-2.5-flash-lite-preview-06-17")
    if ":" in model:
        model = model.split(":")[0]

    try:
        if not any(connection.in_atomic_block for connection in connections.all()):
            connections.close_all()
        client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)
        response = client.chat.completions.create(
            extra_headers=build_openrouter_extra_headers(),
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=LLM_TEMPERATURE,
            max_tokens=MAX_TWEET_TOKENS,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        logger.exception("LLM generation failed")
        return None


def get_generator(tweet_type: str):
    """tweet_type に応じた生成関数を返す。

    各生成関数は _generate_with_retry でラップされ、文字数バリデーションとリトライを行う。

    Returns:
        生成関数 (queue_item -> str | None)。未知の tweet_type の場合は None。
    """
    generator_map = {
        "new_community": lambda qi: _generate_with_retry(
            generate_new_community_tweet,
            qi.community,
            qi.event,
            fallback_fn=_fallback_new_community_tweet,
        ),
        "lt": lambda qi: _generate_with_retry(
            generate_lt_tweet,
            qi.event_detail,
            fallback_fn=_fallback_presentation_tweet,
        ),
        "special": lambda qi: _generate_with_retry(
            generate_special_event_tweet,
            qi.event_detail,
            fallback_fn=lambda detail: _fallback_presentation_tweet(detail, special=True),
        ),
        "daily_reminder": lambda qi: _generate_with_retry(
            generate_daily_reminder_tweet,
            qi.event,
            fallback_fn=_fallback_daily_reminder_tweet,
        ),
        "slide_share": lambda qi: _generate_with_retry(
            generate_slide_share_tweet,
            qi.event_detail,
            fallback_fn=_fallback_slide_share_tweet,
        ),
    }
    return generator_map.get(tweet_type)


TWITTER_IMAGE_WIDTH = 960


def _image_field_url(image_field) -> str:
    """画像フィールドの URL を X 向けサイズで返す。"""
    if not image_field:
        return ""

    custom_domain = getattr(settings, 'AWS_S3_CUSTOM_DOMAIN', '')
    if custom_domain:
        url = f"https://{custom_domain}/{image_field.name}"
        return cloudflare_image_url(url, width=TWITTER_IMAGE_WIDTH)

    if hasattr(image_field, "url"):
        return image_field.url

    return ""


def get_poster_image_url(community) -> str:
    """Community のポスター画像の URL を返す。

    Cloudflare Image Resizing で X 向けサイズ（幅960px）に変換する。
    既存の小さい画像（1000px以下）は拡大されず、そのまま通過する。

    Returns:
        画像URLの文字列。ポスター画像が無い場合は空文字列。
    """
    return _image_field_url(community.poster_image)


def get_tweet_image_url(queue_item) -> str:
    """TweetQueue の添付画像 URL を返す。

    発表資料・記事共有では発表スライド由来のサムネイルを優先し、
    未設定の場合だけ集会ポスターへフォールバックする。
    """
    event_detail = getattr(queue_item, 'event_detail', None)
    if event_detail and getattr(event_detail, 'thumbnail_image', None):
        thumbnail_url = _image_field_url(event_detail.thumbnail_image)
        if thumbnail_url:
            return thumbnail_url

    return get_poster_image_url(queue_item.community)
