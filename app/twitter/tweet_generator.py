"""X (Twitter) 自動告知ツイートの生成

OpenRouter API 経由で LLM を呼び出し、各種告知ツイートを生成する。
既存の twitter/utils.py (テンプレートベース生成) とは独立したモジュール。
"""

import logging
import os

from openai import OpenAI

logger = logging.getLogger(__name__)

MAX_TWEET_TOKENS = 280
LLM_TEMPERATURE = 0.7
SANITIZE_MAX_LENGTH = 200

WEEKDAY_NAMES = {
    "Sun": "日", "Mon": "月", "Tue": "火", "Wed": "水",
    "Thu": "木", "Fri": "金", "Sat": "土",
}


def _sanitize_for_prompt(text: str, max_length: int = SANITIZE_MAX_LENGTH) -> str:
    """プロンプトに埋め込む前のサニタイズ。

    改行・制御文字を除去し、最大長で切り詰める。
    """
    if not text:
        return ""
    text = " ".join(text.split())
    return text[:max_length]


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
        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
        response = client.chat.completions.create(
            extra_headers={
                "HTTP-Referer": "https://vrc-ta-hub.com/",
                "X-Title": "VRC TA Hub",
            },
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


def _format_weekdays(weekdays: list) -> str:
    """曜日リストを日本語文字列に変換する。"""
    return "・".join([WEEKDAY_NAMES.get(d, d) for d in (weekdays or [])])


def _build_hashtag_suffix(community) -> str:
    """ハッシュタグ部分を構築する。"""
    hashtag = f"\n#{community.twitter_hashtag}" if community.twitter_hashtag else ""
    return f"#VRChat技術学術{hashtag}"


def generate_new_community_tweet(community, first_event=None) -> str | None:
    """新規集会の告知ツイートを生成する。

    Args:
        community: Community モデルインスタンス
        first_event: 初回 Event (あれば日程を含める)
    """
    system_prompt = (
        "あなたはVRChat技術学術系集会の告知ツイートを作成する専門家です。"
        "新しい集会がオープンしたことを伝える短いツイートを作成してください。"
    )

    weekdays_str = _format_weekdays(community.weekdays)

    event_info = ""
    if first_event:
        event_info = (
            f"\n初回開催日: {first_event.date.strftime('%Y年%m月%d日')}"
            f" {first_event.start_time.strftime('%H:%M')}~"
        )

    hashtag_suffix = _build_hashtag_suffix(community)
    name = _sanitize_for_prompt(community.name)
    description = _sanitize_for_prompt(community.description) or "(なし)"

    user_prompt = f"""以下の新しいVRChat技術学術系集会の告知ツイートを作成してください。

集会名: {name}
開催曜日: {weekdays_str}曜日
開始時刻: {community.start_time.strftime('%H:%M')}
開催周期: {community.frequency}
紹介: {description}{event_info}

以下のルールを守ってください:
- 280文字以内
- 新しい集会が始まることへの期待感を伝える
- 末尾に以下を含める:
  詳細はこちら https://vrc-ta-hub.com/community/{community.pk}/
  {hashtag_suffix}
- ツイート本文のみ出力（説明不要）
"""
    return _call_llm(system_prompt, user_prompt)


def generate_lt_tweet(event_detail) -> str | None:
    """LT 告知ツイートを生成する。

    Args:
        event_detail: EventDetail モデルインスタンス (detail_type='LT')
    """
    system_prompt = (
        "あなたはVRChat技術学術系集会のLT（ライトニングトーク）告知ツイートを作成する専門家です。"
    )

    event = event_detail.event
    community = event.community
    hashtag_suffix = _build_hashtag_suffix(community)

    name = _sanitize_for_prompt(community.name)
    speaker = _sanitize_for_prompt(event_detail.speaker)
    theme = _sanitize_for_prompt(event_detail.theme)

    user_prompt = f"""以下のLT（ライトニングトーク）の告知ツイートを作成してください。

集会名: {name}
開催日: {event.date.strftime('%Y年%m月%d日')}
開始時刻: {event.start_time.strftime('%H:%M')}
発表者: {speaker}
テーマ: {theme}

以下のルールを守ってください:
- 280文字以内
- LTの内容への期待感を伝える
- 末尾に以下を含める:
  詳細はこちら https://vrc-ta-hub.com/event/{event.pk}/
  {hashtag_suffix}
- ツイート本文のみ出力（説明不要）
"""
    return _call_llm(system_prompt, user_prompt)


def generate_special_event_tweet(event_detail) -> str | None:
    """特別回告知ツイートを生成する。

    Args:
        event_detail: EventDetail モデルインスタンス (detail_type='SPECIAL')
    """
    system_prompt = (
        "あなたはVRChat技術学術系集会の特別イベント告知ツイートを作成する専門家です。"
        "通常回とは違う特別感を伝えてください。"
    )

    event = event_detail.event
    community = event.community
    hashtag_suffix = _build_hashtag_suffix(community)

    name = _sanitize_for_prompt(community.name)
    speaker = _sanitize_for_prompt(event_detail.speaker)
    theme = _sanitize_for_prompt(event_detail.theme)

    user_prompt = f"""以下の特別イベントの告知ツイートを作成してください。

集会名: {name}
開催日: {event.date.strftime('%Y年%m月%d日')}
開始時刻: {event.start_time.strftime('%H:%M')}
発表者: {speaker}
テーマ: {theme}

以下のルールを守ってください:
- 280文字以内
- 特別回ならではのワクワク感を伝える
- 末尾に以下を含める:
  詳細はこちら https://vrc-ta-hub.com/event/{event.pk}/
  {hashtag_suffix}
- ツイート本文のみ出力（説明不要）
"""
    return _call_llm(system_prompt, user_prompt)
