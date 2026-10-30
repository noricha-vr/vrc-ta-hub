"""X 向けの告知本文を安全な Misskey 本文へ変換する。"""

import re
import string


# MFM として残すのは URL と、行頭または空白の直後にある実際のハッシュタグだけ。
# URL の範囲は mfm.js の url パーサーと同じ規則で決める（Misskey が自動リンクする範囲と一致させる）。
# 直前の文字がハッシュタグ等に取り込まれると URL の中身が本文として解釈されるため、
# 変換後も MFM のハッシュタグが必ず止まる文字の直後にある URL だけを保護する。
# mfm.js の空白は半角・全角スペースとタブだけ（NBSP などは区切りにならない）。
# 全角化する # : [ ] < > は変換後に区切りの役目を失うので含めない。
# ) ） 」 は対応する開き括弧があるとハッシュタグに取り込まれるので含めない（】は取り込まれない）。
_URL_START_PATTERN = r"(?:(?<=^)|(?<=[ \u3000\t\n.,!?'\"/(（「【】]))https?://"
_HASHTAG_PATTERN = r"(?<!\S)#(?=\w*[^\d\W])\w+"
_LITERAL_PATTERN = re.compile(f"{_URL_START_PATTERN}|{_HASHTAG_PATTERN}", re.MULTILINE)
_URL_CHARACTERS = frozenset(string.ascii_letters + string.digits + ".,_/:%#@$&?!~=+-")
_URL_BRACKETS = {"(": ")", "[": "]"}
# mfm.js の入れ子上限は 20。保護範囲が MFM より広いと無害化が漏れるため、1 段浅く打ち切る。
_URL_BRACKET_DEPTH_LIMIT = 19

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


def _url_end(text: str, start: int, depth: int = 0) -> int:
    """mfm.js と同じく、URL 文字と対応の取れた () / [] が続く範囲の終端を返す。"""
    index = start
    while index < len(text):
        character = text[index]
        if character in _URL_CHARACTERS:
            index += 1
            continue
        if character in _URL_BRACKETS and depth < _URL_BRACKET_DEPTH_LIMIT:
            closing = _URL_BRACKETS[character]
            inner_end = _url_end(text, index + 1, depth + 1)
            if inner_end < len(text) and text[inner_end] == closing:
                index = inner_end + 1
                continue
        break
    return index


def _literal_end(text: str, match: re.Match) -> int:
    """保護区間の終端。URL は末尾の . , を含めない（mfm.js と同じ）。"""
    if not match.group().startswith("http"):
        return match.end()
    end = _url_end(text, match.end())
    while end > match.end() and text[end - 1] in ".,":
        end -= 1
    return end if end > match.end() else match.start()


def to_misskey_text(text: str) -> str:
    """X アカウントはリンクにし、許可した URL / ハッシュタグ以外の MFM を無害化する。"""
    # mfm.js は CR も行末として扱うため、検索構文の判定前に LF へそろえる。
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _neutralize_search_syntax(text)
    text = re.sub(r"（@([A-Za-z0-9_]+)）", r"（https://x.com/\1）", text)
    parts = []
    end = 0
    for match in _LITERAL_PATTERN.finditer(text):
        if match.start() < end:
            continue
        literal_end = _literal_end(text, match)
        if literal_end <= match.start():
            continue
        parts.append(_neutralize_markup(text[end:match.start()]))
        parts.append(text[match.start():literal_end])
        end = literal_end
    parts.append(_neutralize_markup(text[end:]))
    return "".join(parts)
