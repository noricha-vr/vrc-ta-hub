"""X (Twitter) 自動告知ツイートの生成

OpenRouter API 経由で LLM を呼び出し、各種告知ツイートを生成する。
既存の twitter/utils.py (テンプレートベース生成) とは独立したモジュール。
"""

import logging
import os
import re

from django.conf import settings
from openai import OpenAI

from ta_hub.libs import cloudflare_image_url

logger = logging.getLogger(__name__)

MAX_TWEET_TOKENS = 280
LLM_TEMPERATURE = 0.7
SANITIZE_MAX_LENGTH = 200

TWEET_MAX_WEIGHTED_LENGTH = 280
URL_WEIGHTED_LENGTH = 23
RETRY_TARGET_CHARS_STEP = 20

WEEKDAY_NAMES = {
    "Sun": "日", "Mon": "月", "Tue": "火", "Wed": "水",
    "Thu": "木", "Fri": "金", "Sat": "土",
}


def count_tweet_length(text: str) -> int:
    """Twitter/X の重み付きカウント方式で文字数を返す。

    - URL (https?://\S+): 常に23としてカウント
    - U+0000〜U+10FF の文字: 重み1
    - U+1100 以上の文字 (CJK等): 重み2
    """
    url_pattern = re.compile(r"https?://\S+")
    urls = url_pattern.findall(text)
    text_without_urls = url_pattern.sub("", text)

    weight = 0
    for ch in text_without_urls:
        weight += 2 if ord(ch) >= 0x1100 else 1

    weight += len(urls) * URL_WEIGHTED_LENGTH
    return weight


def _generate_with_retry(generate_fn, *args, max_retries=3, **kwargs) -> str | None:
    """生成関数をリトライラッパーで実行する。

    1. target_chars=140 で生成
    2. count_tweet_length() でバリデーション（上限 TWEET_MAX_WEIGHTED_LENGTH）
    3. 超過していたら target_chars を RETRY_TARGET_CHARS_STEP ずつ減らしてリトライ
    4. max_retries 回リトライ後も超過している場合は最後の結果を返す
    """
    target_chars = 140
    result = None

    for attempt in range(max_retries + 1):
        result = generate_fn(*args, target_chars=target_chars, **kwargs)
        if result is None:
            return None

        length = count_tweet_length(result)
        if length <= TWEET_MAX_WEIGHTED_LENGTH:
            if attempt > 0:
                logger.info(
                    "Tweet length OK after %d retries (weighted=%d, target_chars=%d)",
                    attempt,
                    length,
                    target_chars,
                )
            return result

        logger.warning(
            "Tweet length exceeded (weighted=%d > %d, attempt=%d/%d, target_chars=%d). Retrying.",
            length,
            TWEET_MAX_WEIGHTED_LENGTH,
            attempt + 1,
            max_retries + 1,
            target_chars,
        )
        target_chars -= RETRY_TARGET_CHARS_STEP

    return result


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


def generate_new_community_tweet(community, first_event=None, target_chars=140) -> str | None:
    """新規集会の告知ツイートを生成する。

    Args:
        community: Community モデルインスタンス
        first_event: 初回 Event (あれば日程を含める)
        target_chars: LLM に指示する目標文字数
    """
    system_prompt = (
        "あなたはVRChat技術学術系集会の告知ツイートを作成するライターです。"
        "「参加したい」と思わせる告知を書いてください。"
    )

    weekdays_str = _format_weekdays(community.weekdays)

    event_info = ""
    if first_event:
        event_info = (
            f"\n初回開催日: {first_event.date.strftime('%m/%d')}({WEEKDAY_NAMES.get(first_event.date.strftime('%a'), '')})"
            f" {first_event.start_time.strftime('%H:%M')}~"
        )

    hashtag_suffix = _build_hashtag_suffix(community)
    name = _sanitize_for_prompt(community.name)
    description = _sanitize_for_prompt(community.description) or "(なし)"

    user_prompt = f"""以下の新しいVRChat集会の告知ツイートを作成してください。

集会名: {name}
開催: {community.frequency} {weekdays_str}曜日 {community.start_time.strftime('%H:%M')}~
紹介: {description}{event_info}

## 必須要素（必ず本文に含めること）
1. 集会名
2. 開催スケジュール（曜日・時刻）
3. どんな人向けか / 何が得られるか（紹介文から1行で）

## スタイル
- {target_chars}文字以内（URLやハッシュタグ含む。日本語は1文字としてカウント）
- 「こんな集会が始まりました」ではなく「こういう人は来て」というトーン
- 末尾に以下を必ず含める:
  詳細はこちら https://vrc-ta-hub.com/community/{community.pk}/
  {hashtag_suffix}
- ツイート本文のみ出力（説明不要）
"""
    return _call_llm(system_prompt, user_prompt)


def generate_lt_tweet(event_detail, target_chars=140) -> str | None:
    """LT 告知ツイートを生成する。

    Args:
        event_detail: EventDetail モデルインスタンス (detail_type='LT')
        target_chars: LLM に指示する目標文字数
    """
    system_prompt = (
        "あなたはVRChat集会のLT告知ツイートを書くライターです。"
        "読んだ人が「聞きたい」「行きたい」と思う告知を書いてください。"
    )

    event = event_detail.event
    community = event.community
    hashtag_suffix = _build_hashtag_suffix(community)
    weekday = WEEKDAY_NAMES.get(event.date.strftime("%a"), "")

    name = _sanitize_for_prompt(community.name)
    speaker = _sanitize_for_prompt(event_detail.speaker)
    theme = _sanitize_for_prompt(event_detail.theme)

    user_prompt = f"""以下のLT（ライトニングトーク）の告知ツイートを作成してください。

集会名: {name}
日時: {event.date.strftime('%m/%d')}({weekday}) {event.start_time.strftime('%H:%M')}~
発表者: {speaker}
テーマ: {theme}

## 必須要素（必ず本文に含めること）
1. 開催日時（「{event.date.strftime('%m/%d')}({weekday}) {event.start_time.strftime('%H:%M')}~」の形式で）
2. 発表テーマ（「{theme}」をそのまま記載。言い換え・要約禁止）
3. 発表者名
4. 「このテーマが気になる人は聞きに来て」という呼びかけ

## スタイル
- {target_chars}文字以内（URLやハッシュタグ含む。日本語は1文字としてカウント）
- テーマ名をそのまま書いた上で、何が聞けるかを1文で補足する
- 末尾に以下を必ず含める:
  詳細はこちら https://vrc-ta-hub.com/community/{community.pk}/
  {hashtag_suffix}
- ツイート本文のみ出力（説明不要）
"""
    return _call_llm(system_prompt, user_prompt)


def generate_slide_share_tweet(event_detail, target_chars=140) -> str | None:
    """スライド/記事共有ツイートを生成する。

    Args:
        event_detail: EventDetail モデルインスタンス（slide_url または youtube_url が設定済み）
        target_chars: LLM に指示する目標文字数
    """
    system_prompt = (
        "あなたはVRChat集会の発表資料を紹介するライターです。"
        "「この資料、読んでみたい」と思わせるツイートを書いてください。"
    )

    event = event_detail.event
    community = event.community
    hashtag_suffix = _build_hashtag_suffix(community)

    name = _sanitize_for_prompt(community.name)
    speaker = _sanitize_for_prompt(event_detail.speaker)
    theme = _sanitize_for_prompt(event_detail.theme)

    # 公開リソースの種類をフラグで判定（URLはプロンプトに含めない）
    resources = []
    if event_detail.slide_url or event_detail.slide_file:
        resources.append("スライド")
    if event_detail.youtube_url:
        resources.append("動画")
    resources_text = "・".join(resources)

    user_prompt = f"""以下の発表の{resources_text}が公開されたことを伝えるツイートを作成してください。

集会名: {name}
発表者: {speaker}
テーマ: {theme}
公開された資料: {resources_text}

## 必須要素（必ず本文に含めること）
1. 発表テーマ（「{theme}」をそのまま記載。言い換え・要約禁止）
2. {resources_text}が公開されたこと
3. 「こういう人はチェックして」という呼びかけ

## スタイル
- {target_chars}文字以内（URLやハッシュタグ含む。日本語は1文字としてカウント）
- 日付は不要（過去のイベントなので）
- テーマ名をそのまま書いた上で、「読むと何がわかるか」を1文で補足する
- 末尾に以下を必ず含める:
  詳細はこちら https://vrc-ta-hub.com/event/{event.pk}/
  {hashtag_suffix}
- ツイート本文のみ出力（説明不要）
"""
    return _call_llm(system_prompt, user_prompt)


def get_generator(tweet_type: str):
    """tweet_type に応じた生成関数を返す。

    各生成関数は _generate_with_retry でラップされ、文字数バリデーションとリトライを行う。

    Returns:
        生成関数 (queue_item -> str | None)。未知の tweet_type の場合は None。
    """
    generator_map = {
        "new_community": lambda qi: _generate_with_retry(
            generate_new_community_tweet, qi.community, qi.event
        ),
        "lt": lambda qi: _generate_with_retry(generate_lt_tweet, qi.event_detail),
        "special": lambda qi: _generate_with_retry(
            generate_special_event_tweet, qi.event_detail
        ),
        "slide_share": lambda qi: _generate_with_retry(
            generate_slide_share_tweet, qi.event_detail
        ),
    }
    return generator_map.get(tweet_type)


TWITTER_IMAGE_WIDTH = 960


def get_poster_image_url(community) -> str:
    """Community のポスター画像の URL を返す。

    Cloudflare Image Resizing で Twitter 推奨サイズ（幅960px）に変換する。
    既存の小さい画像（1000px以下）は拡大されず、そのまま通過する。

    Returns:
        画像URLの文字列。ポスター画像が無い場合は空文字列。
    """
    poster = community.poster_image
    if not poster:
        return ""

    custom_domain = getattr(settings, 'AWS_S3_CUSTOM_DOMAIN', '')
    if custom_domain:
        url = f"https://{custom_domain}/{poster.name}"
        return cloudflare_image_url(url, width=TWITTER_IMAGE_WIDTH)

    if hasattr(poster, "url"):
        return poster.url

    return ""


def generate_special_event_tweet(event_detail, target_chars=140) -> str | None:
    """特別回告知ツイートを生成する。

    Args:
        event_detail: EventDetail モデルインスタンス (detail_type='SPECIAL')
        target_chars: LLM に指示する目標文字数
    """
    system_prompt = (
        "あなたはVRChat集会の特別イベント告知ツイートを書くライターです。"
        "通常回とは違う特別な回であることを伝え、「行きたい」と思わせてください。"
    )

    event = event_detail.event
    community = event.community
    hashtag_suffix = _build_hashtag_suffix(community)
    weekday = WEEKDAY_NAMES.get(event.date.strftime("%a"), "")

    name = _sanitize_for_prompt(community.name)
    speaker = _sanitize_for_prompt(event_detail.speaker)
    theme = _sanitize_for_prompt(event_detail.theme)

    user_prompt = f"""以下の特別イベントの告知ツイートを作成してください。

集会名: {name}
日時: {event.date.strftime('%m/%d')}({weekday}) {event.start_time.strftime('%H:%M')}~
発表者/ゲスト: {speaker}
テーマ: {theme}

## 必須要素（必ず本文に含めること）
1. 「特別回」であること
2. 開催日時（「{event.date.strftime('%m/%d')}({weekday}) {event.start_time.strftime('%H:%M')}~」の形式で）
3. 発表テーマ（「{theme}」をそのまま記載。言い換え・要約禁止）
4. 発表者/ゲスト名
5. 「このテーマに興味ある人は来て」という呼びかけ

## スタイル
- {target_chars}文字以内（URLやハッシュタグ含む。日本語は1文字としてカウント）
- テーマ名をそのまま書いた上で、特別回ならではの見どころを1文で補足する
- 末尾に以下を必ず含める:
  詳細はこちら https://vrc-ta-hub.com/community/{community.pk}/
  {hashtag_suffix}
- ツイート本文のみ出力（説明不要）
"""
    return _call_llm(system_prompt, user_prompt)
