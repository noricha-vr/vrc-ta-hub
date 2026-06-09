"""ツイート生成の共通ユーティリティ。

- X の重み付き文字数カウント・本文行数カウント
- 投稿前バリデーション (文字数 / 本文行数)
- プロンプト用サニタイズ
- 登壇者表示・曜日整形・ハッシュタグ整形
- 決定的フォールバックで使う本文ビルダー (`_build_tweet` / `_trim_to_weight` / `_fit_candidate`)
"""

import re

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

BODY_LINE_CONSTRAINT = (
    "- **本文は3行以内に収める（X のスパムフィルタ回避のため必須）**\n"
    "  - 「本文」= URL行・ハッシュタグ行・空行を除いた実質的な告知テキスト行\n"
    "  - 本文4行以上 + URL の組み合わせは X API が 403 で拒否する\n"
    "  - 補足説明と誘導文は別行に分けず1行に統合する"
)


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


def _validation_feedback(text: str) -> str:
    length = count_tweet_length(text)
    body_lines = count_body_lines(text)
    overage = max(0, length - TWEET_MAX_WEIGHTED_LENGTH)
    return (
        f"前回出力は weighted_length={length}/{TWEET_MAX_WEIGHTED_LENGTH}, "
        f"body_lines={body_lines}/{MAX_BODY_LINES} でした。"
        f"{overage} weighted 以上短くし、補足文を最優先で削ってください。"
    )


def _sanitize_for_prompt(text: str, max_length: int = SANITIZE_MAX_LENGTH) -> str:
    """プロンプトに埋め込む前のサニタイズ。

    改行・制御文字を除去し、最大長で切り詰める。
    """
    if not text:
        return ""
    text = " ".join(text.split())
    return text[:max_length]


def _format_speaker_display(event_detail) -> str:
    """登壇者名を告知本文用の敬称付き表記で返す。"""
    speaker = _sanitize_for_prompt(event_detail.speaker)
    display_name = f"{speaker}さん"
    applicant = getattr(event_detail, "applicant", None)
    x_account = _sanitize_for_prompt(getattr(applicant, "x_account", ""))
    if x_account:
        return f"{display_name}（@{x_account}）"
    return display_name


def _format_weekdays(weekdays: list) -> str:
    """曜日リストを日本語文字列に変換する。"""
    return "・".join([WEEKDAY_NAMES.get(d, d) for d in (weekdays or [])])


def _build_hashtag_suffix(community) -> str:
    """ハッシュタグ部分を構築する。"""
    hashtag = f"#{community.twitter_hashtag}\n" if community.twitter_hashtag else ""
    return f"{hashtag}#VRChat技術学術"
