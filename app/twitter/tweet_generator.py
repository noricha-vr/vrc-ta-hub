"""X 自動告知ポストの生成

OpenRouter API 経由で LLM を呼び出し、各種告知ポストを生成する。
既存の twitter/utils.py (テンプレートベース生成) とは独立したモジュール。
"""

import inspect
import logging
import os
import re

from django.conf import settings
from django.db import connections
from openai import OpenAI

from ta_hub.libs import cloudflare_image_url
from website.constants import OPENROUTER_BASE_URL, build_site_url

logger = logging.getLogger(__name__)

MAX_TWEET_TOKENS = 280
LLM_TEMPERATURE = 0.7
SANITIZE_MAX_LENGTH = 200

TWEET_MAX_WEIGHTED_LENGTH = 280
URL_WEIGHTED_LENGTH = 23
RETRY_TARGET_CHARS_STEP = 20
MAX_BODY_LINES = 3
TRUNCATION_SUFFIX = "..."

WEEKDAY_NAMES = {
    "Sun": "日", "Mon": "月", "Tue": "火", "Wed": "水",
    "Thu": "木", "Fri": "金", "Sat": "土",
}


def count_tweet_length(text: str) -> int:
    """X の重み付きカウント方式で文字数を返す。

    - URL (https?://\\S+): 常に23としてカウント
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


def count_body_lines(text: str) -> int:
    """URL行・ハッシュタグ行・空行を除いた本文行数を返す。

    X API は「本文4行以上 + URL」の組み合わせをスパムフィルタで弾くため、
    本文行を MAX_BODY_LINES 以下に保つバリデーションに用いる。
    """
    count = 0
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if "https://" in stripped or "http://" in stripped:
            continue
        count += 1
    return count


def validate_tweet_text(text: str) -> list[str]:
    """投稿前に満たすべき X 向け制約の違反理由を返す。"""
    errors = []
    length = count_tweet_length(text)
    if length > TWEET_MAX_WEIGHTED_LENGTH:
        errors.append(f"weighted_length={length}>{TWEET_MAX_WEIGHTED_LENGTH}")

    body_lines = count_body_lines(text)
    if body_lines > MAX_BODY_LINES:
        errors.append(f"body_lines={body_lines}>{MAX_BODY_LINES}")

    return errors


def is_tweet_text_valid(text: str) -> bool:
    """生成済み本文が X 投稿前制約を満たすか返す。"""
    return not validate_tweet_text(text)


def _build_tweet(body_lines: list[str], url: str, hashtag_suffix: str) -> str:
    lines = [line.strip() for line in body_lines if line and line.strip()]
    if url:
        lines.extend(["", url.strip()])
    hashtags = [line.strip() for line in hashtag_suffix.splitlines() if line.strip()]
    lines.extend(hashtags)
    return "\n".join(lines)


def _trim_to_weight(text: str, max_weight: int) -> str:
    if max_weight <= 0:
        return ""
    if count_tweet_length(text) <= max_weight:
        return text

    suffix = TRUNCATION_SUFFIX
    suffix_weight = count_tweet_length(suffix)
    if max_weight <= suffix_weight:
        suffix = ""
        suffix_weight = 0

    chars = []
    current = 0
    for ch in text:
        weight = 2 if ord(ch) >= 0x1100 else 1
        if current + weight + suffix_weight > max_weight:
            break
        chars.append(ch)
        current += weight

    return "".join(chars).rstrip() + suffix


def _fit_candidate(
    body_lines: list[str],
    url: str,
    hashtag_suffix: str,
    trim_indexes: list[int],
) -> str | None:
    candidate = _build_tweet(body_lines, url, hashtag_suffix)
    if is_tweet_text_valid(candidate):
        return candidate

    fitted_lines = body_lines[:]
    for index in trim_indexes:
        if index >= len(fitted_lines):
            continue

        for _ in range(3):
            candidate = _build_tweet(fitted_lines, url, hashtag_suffix)
            overage = count_tweet_length(candidate) - TWEET_MAX_WEIGHTED_LENGTH
            if overage <= 0:
                break

            current_line = fitted_lines[index]
            current_weight = count_tweet_length(current_line)
            fitted_lines[index] = _trim_to_weight(current_line, current_weight - overage)
            if fitted_lines[index] == current_line:
                break

        candidate = _build_tweet(fitted_lines, url, hashtag_suffix)
        if is_tweet_text_valid(candidate):
            return candidate

    return None


def _fallback_new_community_tweet(community, first_event=None) -> str | None:
    weekdays_str = _format_weekdays(community.weekdays)
    name = _sanitize_for_prompt(community.name)
    url = f"詳細はこちら {build_site_url(f'/community/{community.pk}/')}"
    hashtag_suffix = _build_hashtag_suffix(community)

    if first_event:
        weekday = WEEKDAY_NAMES.get(first_event.date.strftime("%a"), "")
        schedule = f"{first_event.date.strftime('%-m/%-d')}({weekday}) {first_event.start_time.strftime('%H:%M')}~"
    else:
        schedule = f"{community.frequency} {weekdays_str}曜日 {community.start_time.strftime('%H:%M')}~"

    body_lines = [
        f"新しい集会「{name}」がはじまります",
        schedule,
    ]
    return _fit_candidate(body_lines, url, hashtag_suffix, [0])


def _fallback_presentation_tweet(event_detail, *, special: bool = False) -> str | None:
    event = event_detail.event
    community = event.community
    weekday = WEEKDAY_NAMES.get(event.date.strftime("%a"), "")
    label = " 特別回" if special else ""
    body_lines = [
        f"{event.date.strftime('%-m/%-d')}({weekday}) {event.start_time.strftime('%H:%M')}~ {_sanitize_for_prompt(community.name)}{label}",
        f"{_sanitize_for_prompt(event_detail.speaker)}さん「{_sanitize_for_prompt(event_detail.theme)}」",
    ]
    url = f"詳細はこちら {build_site_url(f'/community/{community.pk}/')}"
    return _fit_candidate(body_lines, url, _build_hashtag_suffix(community), [1, 0])


def _fallback_slide_share_tweet(event_detail) -> str | None:
    community = event_detail.event.community
    resources = []
    if event_detail.slide_url or event_detail.slide_file:
        resources.append("スライド")
    if event_detail.youtube_url:
        resources.append("動画")
    resources_text = "・".join(resources) or "資料"
    body_lines = [
        (
            f"{_sanitize_for_prompt(community.name)} "
            f"{_sanitize_for_prompt(event_detail.speaker)}さん"
            f"「{_sanitize_for_prompt(event_detail.theme)}」"
        ),
        f"{resources_text}が公開されました",
    ]
    url = f"詳細はこちら {build_site_url(f'/event/detail/{event_detail.pk}/')}"
    return _fit_candidate(body_lines, url, _build_hashtag_suffix(community), [0])


def _fallback_daily_reminder_tweet(event) -> str | None:
    details = list(
        event.details.filter(
            status="approved",
            detail_type__in=("LT", "SPECIAL"),
        ).order_by("start_time", "pk")
    )
    if not details:
        return None

    community = event.community
    name = _sanitize_for_prompt(community.name)
    url = f"詳細はこちら {build_site_url(f'/community/{community.pk}/')}"
    hashtag_suffix = _build_hashtag_suffix(community)

    for count in range(min(3, len(details)), 0, -1):
        body_lines = [f"今夜 {event.start_time.strftime('%H:%M')}〜 {name}"]
        for detail in details[:count]:
            body_lines.append(
                f"{_sanitize_for_prompt(detail.speaker)}さん"
                f"「{_sanitize_for_prompt(detail.theme)}」"
            )
        fitted = _fit_candidate(
            body_lines,
            url,
            hashtag_suffix,
            list(range(len(body_lines) - 1, -1, -1)),
        )
        if fitted:
            return fitted

    return None


def _validation_feedback(text: str) -> str:
    length = count_tweet_length(text)
    body_lines = count_body_lines(text)
    overage = max(0, length - TWEET_MAX_WEIGHTED_LENGTH)
    return (
        f"前回出力は weighted_length={length}/{TWEET_MAX_WEIGHTED_LENGTH}, "
        f"body_lines={body_lines}/{MAX_BODY_LINES} でした。"
        f"{overage} weighted 以上短くし、補足文を最優先で削ってください。"
    )


def _call_generate_fn(generate_fn, *args, target_chars: int, validation_feedback: str, **kwargs):
    parameters = inspect.signature(generate_fn).parameters
    accepts_feedback = (
        "validation_feedback" in parameters
        or any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values())
    )
    if accepts_feedback:
        return generate_fn(
            *args,
            target_chars=target_chars,
            validation_feedback=validation_feedback,
            **kwargs,
        )
    return generate_fn(*args, target_chars=target_chars, **kwargs)


def _generate_with_retry(
    generate_fn,
    *args,
    max_retries=3,
    fallback_fn=None,
    **kwargs,
) -> str | None:
    """生成関数をリトライラッパーで実行する。

    1. target_chars=140 で生成
    2. count_tweet_length() と count_body_lines() でバリデーション
       （文字数 <= TWEET_MAX_WEIGHTED_LENGTH かつ 本文行数 <= MAX_BODY_LINES）
    3. どちらか違反していたら target_chars を RETRY_TARGET_CHARS_STEP ずつ減らしてリトライ
    4. max_retries 回リトライ後も違反している場合は決定的な圧縮にフォールバック
    """
    target_chars = 140
    result = None
    validation_feedback = ""

    for attempt in range(max_retries + 1):
        result = _call_generate_fn(
            generate_fn,
            *args,
            target_chars=target_chars,
            validation_feedback=validation_feedback,
            **kwargs,
        )
        if result is None:
            return None

        errors = validate_tweet_text(result)

        if not errors:
            if attempt > 0:
                logger.info(
                    "Tweet validation OK after %d retries (weighted=%d, body_lines=%d, target_chars=%d)",
                    attempt,
                    count_tweet_length(result),
                    count_body_lines(result),
                    target_chars,
                )
            return result

        logger.warning(
            "Tweet validation failed (%s, attempt=%d/%d, target_chars=%d). Retrying.",
            ", ".join(errors),
            attempt + 1,
            max_retries + 1,
            target_chars,
        )
        validation_feedback = _validation_feedback(result)
        target_chars -= RETRY_TARGET_CHARS_STEP

    if fallback_fn is None:
        return None

    fallback_result = fallback_fn(*args)
    if fallback_result and is_tweet_text_valid(fallback_result):
        logger.info(
            "Tweet deterministic fallback succeeded (weighted=%d, body_lines=%d)",
            count_tweet_length(fallback_result),
            count_body_lines(fallback_result),
        )
        return fallback_result

    logger.error(
        "Tweet deterministic fallback failed after generation retries: %s",
        validate_tweet_text(fallback_result or ""),
    )
    return None


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
        if not any(connection.in_atomic_block for connection in connections.all()):
            connections.close_all()
        client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)
        response = client.chat.completions.create(
            extra_headers={
                "HTTP-Referer": build_site_url("/"),
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
    hashtag = f"#{community.twitter_hashtag}\n" if community.twitter_hashtag else ""
    return f"{hashtag}#VRChat技術学術"


BODY_LINE_CONSTRAINT = (
    "- **本文は3行以内に収める（X のスパムフィルタ回避のため必須）**\n"
    "  - 「本文」= URL行・ハッシュタグ行・空行を除いた実質的な告知テキスト行\n"
    "  - 本文4行以上 + URL の組み合わせは X API が 403 で拒否する\n"
    "  - 補足説明と誘導文は別行に分けず1行に統合する"
)


def generate_new_community_tweet(
    community,
    first_event=None,
    target_chars=140,
    validation_feedback="",
) -> str | None:
    """新規集会の告知ポストを生成する。

    Args:
        community: Community モデルインスタンス
        first_event: 初回 Event (あれば日程を含める)
        target_chars: LLM に指示する目標文字数
    """
    system_prompt = (
        "あなたはVRChat技術学術系集会の告知ポストを作成するライターです。"
        "「参加したい」と思わせる告知を書いてください。"
    )

    weekdays_str = _format_weekdays(community.weekdays)

    event_info = ""
    if first_event:
        event_info = (
            f"\n初回開催日: {first_event.date.strftime('%-m/%-d')}({WEEKDAY_NAMES.get(first_event.date.strftime('%a'), '')})"
            f" {first_event.start_time.strftime('%H:%M')}~"
        )

    hashtag_suffix = _build_hashtag_suffix(community)
    community_url = build_site_url(f"/community/{community.pk}/")
    name = _sanitize_for_prompt(community.name)
    description = _sanitize_for_prompt(community.description) or "(なし)"

    user_prompt = f"""以下の新しいVRChat集会の告知ポストを作成してください。

集会名: {name}
開催: {community.frequency} {weekdays_str}曜日 {community.start_time.strftime('%H:%M')}~
紹介: {description}{event_info}
{validation_feedback}

## 必須要素（必ず本文に含めること）
1. 集会名
2. 開催スケジュール（曜日・時刻）
3. どんな人向けか / 何が得られるか（紹介文から1行で）

## 出力フォーマット（本文は3行以内）

新しい集会「{{集会名}}」がはじまります

{{開催スケジュール}}
{{対象者 + 何が得られるかを1行に統合}}

詳細はこちら {{URL}}
{{ハッシュタグ}}

## スタイル
- {target_chars}文字以内（URLやハッシュタグ含む。日本語は1文字としてカウント）
{BODY_LINE_CONSTRAINT}
- 「こんな集会が始まりました」ではなく「こういう人は来て」というトーン
- 末尾に以下を必ず含める:
  詳細はこちら {community_url}
  {hashtag_suffix}
- 意味のまとまり（日時・テーマ・補足・リンク・ハッシュタグ）ごとに空行を入れて読みやすくする
- ハッシュタグは末尾に指定されたもののみ使用（自分で追加・変形しない）
- 句点（。）を一切使わない（「〜です。」「〜ます。」も禁止。「〜です」「〜ます」で止める）
    - ポスト本文のみ出力（説明不要）
"""
    return _call_llm(system_prompt, user_prompt)


def generate_lt_tweet(event_detail, target_chars=140, validation_feedback="") -> str | None:
    """発表告知ポストを生成する。

    Args:
        event_detail: EventDetail モデルインスタンス (detail_type='LT')
        target_chars: LLM に指示する目標文字数
    """
    system_prompt = (
        "あなたはVRChat集会の発表告知ポストを書くライターです。"
        "読んだ人が「聞きたい」「行きたい」と思う告知を書いてください。"
    )

    event = event_detail.event
    community = event.community
    hashtag_suffix = _build_hashtag_suffix(community)
    community_url = build_site_url(f"/community/{community.pk}/")
    weekday = WEEKDAY_NAMES.get(event.date.strftime("%a"), "")

    name = _sanitize_for_prompt(community.name)
    speaker = _sanitize_for_prompt(event_detail.speaker)
    theme = _sanitize_for_prompt(event_detail.theme)

    user_prompt = f"""以下の発表の告知ポストを作成してください。

集会名: {name}
日時: {event.date.strftime('%-m/%-d')}({weekday}) {event.start_time.strftime('%H:%M')}~
発表者: {speaker}
テーマ: {theme}
{validation_feedback}

## 必須要素（必ず本文に含めること）
1. 集会名（「{name}」）
2. 開催日時（「{event.date.strftime('%-m/%-d')}({weekday}) {event.start_time.strftime('%H:%M')}~」の形式で）
3. 発表テーマ（「{theme}」をそのまま記載。言い換え・要約禁止）
4. 発表者名（敬称は「さん」を付ける）
5. テーマの補足説明と次のアクション（聞きに来る・詳細を見る等）への誘導を**1行にまとめる**（本文3行制約のため別行にしない）

## 出力フォーマット（本文は3行以内）

{{日時}} {{集会名}}

{{発表者}}さん「{{テーマ}}」
{{テーマ補足 + 参加誘導を1行に統合}}

詳細はこちら {{URL}}
{{ハッシュタグ}}

## スタイル
- {target_chars}文字以内（URLやハッシュタグ含む。日本語は1文字としてカウント）
{BODY_LINE_CONSTRAINT}
- テーマ名をそのまま書いた上で、何が聞けるかを1文で補足する
- 誘導の一文は毎回異なる自然な表現にする（「このテーマが気になる人は聞きに来て」のような定型文の繰り返し禁止）
- 末尾に以下を必ず含める:
  詳細はこちら {community_url}
  {hashtag_suffix}
- 意味のまとまり（日時・テーマ・補足・リンク・ハッシュタグ）ごとに空行を入れて読みやすくする
- ハッシュタグは末尾に指定されたもののみ使用（自分で追加・変形しない）
- 句点（。）を一切使わない（「〜です。」「〜ます。」も禁止。「〜です」「〜ます」で止める）
    - ポスト本文のみ出力（説明不要）
"""
    return _call_llm(system_prompt, user_prompt)


def generate_slide_share_tweet(event_detail, target_chars=140, validation_feedback="") -> str | None:
    """スライド/記事共有ポストを生成する。

    Args:
        event_detail: EventDetail モデルインスタンス（slide_url または youtube_url が設定済み）
        target_chars: LLM に指示する目標文字数
    """
    system_prompt = (
        "あなたはVRChat集会の発表資料を紹介するライターです。"
        "「この資料、読んでみたい」と思わせるポストを書いてください。"
    )

    event = event_detail.event
    community = event.community
    hashtag_suffix = _build_hashtag_suffix(community)
    detail_url = build_site_url(f"/event/detail/{event_detail.pk}/")

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

    user_prompt = f"""以下の発表の{resources_text}が公開されたことを伝えるポストを作成してください。

集会名: {name}
発表者: {speaker}
テーマ: {theme}
公開された資料: {resources_text}
{validation_feedback}

## 必須要素（必ず本文に含めること）
1. 集会名（「{name}」）
2. 発表者名（敬称は「さん」を付ける）
3. 発表テーマ（「{theme}」をそのまま記載。言い換え・要約禁止）
4. {resources_text}が公開されたこと
5. 内容の補足と次のアクション（資料を見る・チェックする等）への誘導を**1行にまとめる**（本文3行制約のため別行にしない）

## 出力フォーマット（本文は3行以内）

{{集会名}} {{発表者}}さん「{{テーマ}}」

{{resources_text}}が公開されました
{{内容補足 + 閲覧誘導を1行に統合}}

詳細はこちら {{URL}}
{{ハッシュタグ}}

## スタイル
- {target_chars}文字以内（URLやハッシュタグ含む。日本語は1文字としてカウント）
{BODY_LINE_CONSTRAINT}
- 日付は不要（過去のイベントなので）
- テーマ名をそのまま書いた上で、「読むと何がわかるか」を1文で補足する
- 誘導の一文は毎回異なる自然な表現にする（「〜な方はチェック」のような定型文の繰り返し禁止）
- 末尾に以下を必ず含める:
  詳細はこちら {detail_url}
  {hashtag_suffix}
- 意味のまとまり（日時・テーマ・補足・リンク・ハッシュタグ）ごとに空行を入れて読みやすくする
- ハッシュタグは末尾に指定されたもののみ使用（自分で追加・変形しない）
- 句点（。）を一切使わない（「〜です。」「〜ます。」も禁止。「〜です」「〜ます」で止める）
    - ポスト本文のみ出力（説明不要）
"""
    return _call_llm(system_prompt, user_prompt)


def generate_daily_reminder_tweet(event, target_chars=140, validation_feedback="") -> str | None:
    """当日開催イベントのリマインダーポストを生成する。"""
    approved_details = list(
        event.details.filter(
            status="approved",
            detail_type__in=("LT", "SPECIAL"),
        ).order_by("start_time", "pk")
    )
    if not approved_details:
        logger.warning("No approved LT/SPECIAL details found for Event %s", event.pk)
        return None

    community = event.community
    hashtag_suffix = _build_hashtag_suffix(community)
    community_url = build_site_url(f"/community/{community.pk}/")

    presentations = []
    for detail in approved_details[:3]:
        label = "発表" if detail.detail_type == "LT" else "特別回"
        speaker = _sanitize_for_prompt(detail.speaker)
        theme = _sanitize_for_prompt(detail.theme)
        presentations.append(f"- {label}: {speaker}さん「{theme}」")

    more_count = len(approved_details) - len(presentations)
    extra_line = f"\n- ほか {more_count} 件" if more_count > 0 else ""

    system_prompt = (
        "あなたはVRChat集会の当日リマインダーポストを書くライターです。"
        "「誰が」「どんなテーマで」話すのかを主役にして、読んだ人が「聞きたい」と思える告知を書いてください。"
    )

    name = _sanitize_for_prompt(community.name)
    presentation_count = len(approved_details)
    user_prompt = f"""以下のイベント当日リマインダーポストを作成してください。

集会名: {name}
開催: 今夜 {event.start_time.strftime('%H:%M')}~
登録発表数: {presentation_count}件（※この数字は本文に書かない。背景情報として参照するだけ）
発表一覧:
{chr(10).join(presentations)}{extra_line}
{validation_feedback}

## 出力フォーマット（本文は3行以内、この構造に厳密に従うこと）

### 発表が1件の場合
今夜 {{時刻}}〜 {{集会名}}

{{登壇者1}}さん「{{テーマ1}}」
{{テーマ1の補足 + 参加誘導を1行に統合}}

詳細はこちら {{URL}}
{{ハッシュタグ}}

### 発表が2件の場合
今夜 {{時刻}}〜 {{集会名}}

{{登壇者1}}さん「{{テーマ1}}」
{{登壇者2}}さん「{{テーマ2}}」

詳細はこちら {{URL}}
{{ハッシュタグ}}

### 発表が3件以上の場合（4件以上なら上位3件＋「ほかN件」）
今夜 {{時刻}}〜 {{集会名}}
{{登壇者1}}さん「{{テーマ1}}」
{{登壇者2}}さん「{{テーマ2}}」／{{登壇者3}}さん「{{テーマ3}}」

詳細はこちら {{URL}}
{{ハッシュタグ}}

## ルール
- {target_chars}文字以内（URLやハッシュタグ含む。日本語は1文字としてカウント）
{BODY_LINE_CONSTRAINT}
- 1行目は「今夜 {event.start_time.strftime('%H:%M')}〜 {name}」の形式で、今日の開催であることと時刻が一目で伝わるようにする（日付表記は禁止）
- 各発表は「○○さん「テーマ名」」の形式で記載する
  - テーマ名は発表一覧のものをそのまま使う（言い換え・要約・省略禁止）
  - 登壇者名には「さん」を付ける
- **発表数の言及禁止**: 「発表は1件」「全○件」「○本立て」「N件の発表」など、発表の本数を伝える表現は本文に一切書かない（通常1件なので情報価値がない）
- 発表が1件の場合は、発表行の直後に「テーマの補足 + 参加誘導」を**1行に統合**して入れる
  - 補足と誘導は別行に分けず、1文にまとめる（本文3行制約のため必須）
  - 補足は発表一覧に含まれるキーワードや背景知識から自然に膨らませる（事実の捏造は禁止）
- 発表が2件以上の場合は補足・誘導を省略し、発表行のみ並べる（本文3行に収めるため）
- 発表が4件以上の場合は上位3件を記載し、残りは「ほかN件」としてテーマ3の行末にまとめる
- 散文や自然文で発表内容をまとめない（一覧形式を崩さない）
- 末尾に以下を必ず含める:
  詳細はこちら {community_url}
  {hashtag_suffix}
- 空行の入れ方は**本文3行制約を最優先**に決める
  - 発表1件: 集会名の後・発表ブロックの後に空行（出力例の「### 発表が1件の場合」参照）
  - 発表2件以上: 本文行を詰めて配置（出力例の対応ブロック参照）。URL/ハッシュタグとの間にのみ空行を入れる
- ハッシュタグは末尾に指定されたもののみ使用（自分で追加・変形しない）
- 句点（。）を一切使わない
    - ポスト本文のみ出力（説明不要）
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


def generate_special_event_tweet(event_detail, target_chars=140, validation_feedback="") -> str | None:
    """特別回告知ポストを生成する。

    Args:
        event_detail: EventDetail モデルインスタンス (detail_type='SPECIAL')
        target_chars: LLM に指示する目標文字数
    """
    system_prompt = (
        "あなたはVRChat集会の特別イベント告知ポストを書くライターです。"
        "通常回とは違う特別な回であることを伝え、「行きたい」と思わせてください。"
    )

    event = event_detail.event
    community = event.community
    hashtag_suffix = _build_hashtag_suffix(community)
    community_url = build_site_url(f"/community/{community.pk}/")
    weekday = WEEKDAY_NAMES.get(event.date.strftime("%a"), "")

    name = _sanitize_for_prompt(community.name)
    speaker = _sanitize_for_prompt(event_detail.speaker)
    theme = _sanitize_for_prompt(event_detail.theme)

    user_prompt = f"""以下の特別イベントの告知ポストを作成してください。

集会名: {name}
日時: {event.date.strftime('%-m/%-d')}({weekday}) {event.start_time.strftime('%H:%M')}~
発表者/ゲスト: {speaker}
テーマ: {theme}
{validation_feedback}

## 必須要素（必ず本文に含めること）
1. 集会名（「{name}」）
2. 「特別回」であること
3. 開催日時（「{event.date.strftime('%-m/%-d')}({weekday}) {event.start_time.strftime('%H:%M')}~」の形式で）
4. 発表テーマ（「{theme}」をそのまま記載。言い換え・要約禁止）
5. 発表者/ゲスト名（敬称は「さん」を付ける）
6. テーマの補足説明と次のアクション（聞きに来る・詳細を見る等）への誘導を**1行にまとめる**（本文3行制約のため別行にしない）

## 出力フォーマット（本文は3行以内）

{{日時}} {{集会名}} 特別回

{{発表者}}さん「{{テーマ}}」
{{テーマ補足 + 参加誘導を1行に統合}}

詳細はこちら {{URL}}
{{ハッシュタグ}}

## スタイル
- {target_chars}文字以内（URLやハッシュタグ含む。日本語は1文字としてカウント）
{BODY_LINE_CONSTRAINT}
- テーマ名をそのまま書いた上で、特別回ならではの見どころを1文で補足する
- 誘導の一文は毎回異なる自然な表現にする（「このテーマに興味ある人は来て」のような定型文の繰り返し禁止）
- 末尾に以下を必ず含める:
  詳細はこちら {community_url}
  {hashtag_suffix}
- 意味のまとまり（日時・テーマ・補足・リンク・ハッシュタグ）ごとに空行を入れて読みやすくする
- ハッシュタグは末尾に指定されたもののみ使用（自分で追加・変形しない）
- 句点（。）を一切使わない（「〜です。」「〜ます。」も禁止。「〜です」「〜ます」で止める）
    - ポスト本文のみ出力（説明不要）
"""
    return _call_llm(system_prompt, user_prompt)
