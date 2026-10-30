"""X 向けの告知本文を安全な Misskey 本文へ変換する。"""

import re
import string


# MFM として残すのは URL と、行頭または空白の直後にある実際のハッシュタグだけ。
# URL の末尾のピリオド・カンマは URL に含めない。
_URL_PATTERN = r"(?<![A-Za-z0-9])https?://[.,a-zA-Z0-9_/:%#@$&?!~=+\-]*[a-zA-Z0-9_/:%#@$&?!~=+\-]"
_HASHTAG_PATTERN = r"(?<!\S)#(?=\w*[^\d\W])\w+"
_LITERAL_PATTERN = re.compile(f"{_URL_PATTERN}|{_HASHTAG_PATTERN}")

# 行末の検索キーワードが Misskey の検索構文にならないよう、直前に ZWSP を入れる。
_SEARCH_PATTERN = re.compile(
    r"([ \t\u3000]+)(\[(?:検索|search)\]|(?:検索|search))([ \t\u3000]*)(?=\r?$)",
    re.IGNORECASE | re.MULTILINE,
)

_SAFE_ASCII_PUNCTUATION = frozenset('.,!?\'"()-/%&=+')
_NEUTRALIZED_ASCII_PUNCTUATION = {
    "#": "＃",
    "$": "＄",
    ":": "：",
    ";": "；",
    "@": "＠",
    "[": "［",
    "]": "］",
    "\\": "＼",
    "^": "＾",
    "_": "＿",
    "`": "｀",
    "{": "｛",
    "|": "｜",
    "}": "｝",
    "~": "～",
    "*": "＊",
    "<": "＜",
    ">": "＞",
}
_MFM_TRANSLATION = str.maketrans({
    character: _NEUTRALIZED_ASCII_PUNCTUATION[character]
    for character in string.punctuation
    if character not in _SAFE_ASCII_PUNCTUATION
})


def _neutralize_markup(text: str) -> str:
    """許可した ASCII 記号以外を、MFM として解釈されない文字へ置換する。"""
    return text.translate(_MFM_TRANSLATION)


def _neutralize_search_syntax(text: str) -> str:
    return _SEARCH_PATTERN.sub(
        lambda match: f"{match.group(1)}\u200b{match.group(2)}{match.group(3)}",
        text,
    )


def to_misskey_text(text: str) -> str:
    """X アカウントはリンクにし、許可した URL / ハッシュタグ以外の MFM を無害化する。"""
    text = _neutralize_search_syntax(text)
    text = re.sub(r"（@([A-Za-z0-9_]+)）", r"（https://x.com/\1）", text)
    parts = []
    end = 0
    for match in _LITERAL_PATTERN.finditer(text):
        parts.append(_neutralize_markup(text[end:match.start()]))
        parts.append(match.group())
        end = match.end()
    parts.append(_neutralize_markup(text[end:]))
    return "".join(parts)
