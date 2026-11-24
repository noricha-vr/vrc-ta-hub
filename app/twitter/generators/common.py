"""ツイート生成の共通ユーティリティ。

- X の重み付き文字数カウント・本文行数カウント
- 投稿前バリデーション (文字数 / 本文行数)
- プロンプト用サニタイズ
- 登壇者表示・曜日整形・ハッシュタグ整形
- 決定的フォールバックで使う本文ビルダー (`_build_tweet` / `_trim_to_weight` / `_fit_candidate`)
"""

import re
import unicodedata
from pathlib import Path

from community.constants import WEEKDAY_ABBR

MAX_TWEET_TOKENS = 280
LLM_TEMPERATURE = 0.7
SANITIZE_MAX_LENGTH = 200

TWEET_MAX_WEIGHTED_LENGTH = 280
URL_WEIGHTED_LENGTH = 23
RETRY_TARGET_CHARS_STEP = 20
MAX_BODY_LINES = 3
TRUNCATION_SUFFIX = "..."

# 曜日略称の正本は community.constants。ツイート本文では 'Other'（その他）を
# 曜日として表示しないため除外する。
WEEKDAY_NAMES = {
    code: label for code, label in WEEKDAY_ABBR.items() if code != "Other"
}

BODY_LINE_CONSTRAINT = (
    "- **本文は3行以内に収める（X のスパムフィルタ回避のため必須）**\n"
    "  - 「本文」= URL行・ハッシュタグ行・空行を除いた実質的な告知テキスト行\n"
    "  - 本文4行以上 + URL の組み合わせは X API が 403 で拒否する\n"
    "  - 補足説明と誘導文は別行に分けず1行に統合する"
)

GROUP_ID_LINE_PREFIX = "VRChatグループID:"
PROFILE_LINK_FALLBACK_LINE = "詳細・参加方法はプロフィールのリンクへ"
_IANA_TLDS = frozenset(
    line.strip().lower()
    for line in Path(__file__).with_name("iana_tlds.txt").read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.startswith("#")
)
_SPECIAL_TLDS = frozenset({"onion"})
_ASCII_TLDS = frozenset(t for t in _IANA_TLDS if not t.startswith("xn--")) | _SPECIAL_TLDS
_UNICODE_TLDS = frozenset(
    unicodedata.normalize("NFC", t.encode("ascii").decode("idna")).casefold()
    for t in _IANA_TLDS
    if t.startswith("xn--")
)
_UNICODE_TLD_PREFIX_RE = re.compile(
    "^(?:"
    + "|".join(re.escape(t) for t in sorted(_UNICODE_TLDS, key=len, reverse=True))
    + ")"
)
_SCHEME_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_ASCII_TLD_PREFIX_RE = re.compile(
    r"^(xn--[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*|[A-Za-z]{2,24})(?:[^A-Za-z0-9-]|$)",
    re.IGNORECASE,
)


def _is_label_start(ch: str) -> bool:
    """ドメインラベル先頭文字として許可される文字（`_` 以外の英数字・Unicode文字）かを返す。"""
    return ch != "_" and ch.isalnum()


def _is_label_cont(ch: str) -> bool:
    """ドメインラベル内（`_`/`-` 除く）の継続文字（結合文字含む）かを返す。"""
    return ch != "_" and (ch.isalnum() or unicodedata.category(ch) in ("Mn", "Mc"))


def _scan_label(text: str, pos: int) -> int:
    """`pos` 開始の 1 ラベル（`label_start cont* ([-_]+ cont+)*`）を読み進めた終了位置を返す。"""
    n = len(text)
    pos += 1
    while True:
        while pos < n and _is_label_cont(text[pos]):
            pos += 1
        sep = pos
        while sep < n and text[sep] in ("-", "_"):
            sep += 1
        if sep > pos and sep < n and _is_label_cont(text[sep]):
            pos = sep + 1
            continue
        return pos


def _iter_domain_runs(text: str):
    """テキスト内のドット区切りドメイン候補列 `(start, end, parts)` を線形走査で返す。"""
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if not _is_label_start(ch):
            i += 1
            continue
        if i > 0 and (_is_label_cont(text[i - 1]) or text[i - 1] in ("_", ".", "-")):
            i += 1
            continue

        start = i
        pos = _scan_label(text, i)
        parts = [text[i:pos]]
        while pos + 1 < n and text[pos] == "." and _is_label_start(text[pos + 1]):
            label_start = pos + 1
            pos = _scan_label(text, label_start)
            parts.append(text[label_start:pos])

        if len(parts) >= 2:
            yield start, pos, parts
        i = pos


def _is_valid_tld_label(label: str) -> bool:
    """単一ラベルが有効な IANA / 特殊用途 / Punycode / IDN TLD として解釈されるかを返す。"""
    nfc = unicodedata.normalize("NFC", label)
    folded = nfc.casefold()
    unicode_match = _UNICODE_TLD_PREFIX_RE.match(folded)
    if unicode_match:
        matched_tld = unicode_match.group(0)
        rest = folded[len(matched_tld) :]
        is_ascii_word_cont = (
            matched_tld[-1].isascii()
            and bool(rest)
            and rest[0].isascii()
            and (rest[0].isalnum() or rest[0] == "-")
        )
        is_combining_cont = bool(rest) and unicodedata.category(rest[0]) in ("Mn", "Mc")
        if not is_ascii_word_cont and not is_combining_cont:
            return True

    ascii_match = _ASCII_TLD_PREFIX_RE.match(nfc)
    if ascii_match:
        ascii_tld = ascii_match.group(1).lower()
        return ascii_tld.startswith("xn--") or ascii_tld in _ASCII_TLDS

    return False


def _last_valid_tld_index(parts: list[str]) -> int | None:
    """ドット区切りラベル列のうち、URL 扱いされる最右 TLD の位置を返す。"""
    last_idx = None
    for idx in range(1, len(parts)):
        # twitter-text の validDomainName は TLD 直前ラベルに '_' を許可しない
        if "_" not in parts[idx - 1] and _is_valid_tld_label(parts[idx]):
            last_idx = idx
    return last_idx


def contains_url(text: str) -> bool:
    """X API がリンク付き投稿扱いする URL・ドメイン表現を含むか判定する。"""
    if not text:
        return False
    if _SCHEME_URL_RE.search(text):
        return True
    return any(
        _last_valid_tld_index(parts) is not None
        for _, _, parts in _iter_domain_runs(text)
    )


def defang_urls_for_x(text: str) -> str:
    """URL/ドメイン扱いされる表記（ASP.NET / Socket.IO / discord.py 等）を X 非リンク表記へ変換する。"""
    if not text:
        return text
    cleaned = _SCHEME_URL_RE.sub(
        lambda m: re.sub(r"^https?://", "", m.group(0), flags=re.IGNORECASE),
        text,
    )

    chunks: list[str] = []
    cursor = 0
    for start, end, parts in _iter_domain_runs(cleaned):
        last_idx = _last_valid_tld_index(parts)
        if last_idx is None:
            continue
        chunks.append(cleaned[cursor:start])
        head = "・".join(parts[: last_idx + 1])
        tail = ".".join(parts[last_idx + 1 :])
        chunks.append(f"{head}.{tail}" if tail else head)
        cursor = end

    if cursor == 0:
        return cleaned
    chunks.append(cleaned[cursor:])
    return "".join(chunks)


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
    """メタ案内行（URL・グループID・定型誘導・ハッシュタグ・空行）を除いた本文行数を返す。

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
        if stripped.startswith(GROUP_ID_LINE_PREFIX) or stripped == PROFILE_LINK_FALLBACK_LINE:
            continue
        count += 1
    return count


def validate_tweet_text(text: str, *, forbid_urls: bool = False) -> list[str]:
    """投稿前に満たすべき X 向け制約の違反理由を返す。"""
    errors = []
    length = count_tweet_length(text)
    if length > TWEET_MAX_WEIGHTED_LENGTH:
        errors.append(f"weighted_length={length}>{TWEET_MAX_WEIGHTED_LENGTH}")

    body_lines = count_body_lines(text)
    if body_lines > MAX_BODY_LINES:
        errors.append(f"body_lines={body_lines}>{MAX_BODY_LINES}")

    if forbid_urls and contains_url(text):
        errors.append("contains_url")

    return errors


def is_tweet_text_valid(text: str, *, forbid_urls: bool = False) -> bool:
    """生成済み本文が X 投稿前制約を満たすか返す。"""
    return not validate_tweet_text(text, forbid_urls=forbid_urls)


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
    *,
    forbid_urls: bool = False,
) -> str | None:
    candidate = _build_tweet(body_lines, url, hashtag_suffix)
    if is_tweet_text_valid(candidate, forbid_urls=forbid_urls):
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
        if is_tweet_text_valid(candidate, forbid_urls=forbid_urls):
            return candidate

    return None


def _validation_feedback(text: str, *, forbid_urls: bool = False) -> str:
    length = count_tweet_length(text)
    body_lines = count_body_lines(text)
    overage = max(0, length - TWEET_MAX_WEIGHTED_LENGTH)
    parts = [
        f"前回出力は weighted_length={length}/{TWEET_MAX_WEIGHTED_LENGTH}, "
        f"body_lines={body_lines}/{MAX_BODY_LINES} でした。"
        f"{overage} weighted 以上短くし、補足文を最優先で削ってください。"
    ]
    if forbid_urls and contains_url(text):
        parts.append(
            "URL やドメイン（vrc.group やサイトURL等）は一切含めず、指定の末尾行だけを出力してください。"
        )
    return " ".join(parts)


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
