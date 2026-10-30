"""X 向けの告知本文を安全な Misskey 本文へ変換する。"""

import re


# mfm.js の URL / hashtag の文字集合に合わせて保護する。
# URL の末尾の . , は含めない。日本語や括弧の後ろは通常テキストになる。
_LITERAL_PATTERN = re.compile(
    r'''https?://[.,a-zA-Z0-9_/:%#@$&?!~=+\-]*[a-zA-Z0-9_/:%#@$&?!~=+\-]'''
    r'''|#[^\s.,!?'"#:/\[\]【】()「」（）<>]+'''
)
_MFM_TRANSLATION = str.maketrans('[]*~`_$<>@\\', '［］＊～｀＿＄＜＞＠＼')


def _neutralize_markup(text: str) -> str:
    # 時刻などの : は保持し、カスタム絵文字の区切りだけを無害化する。
    text = re.sub(r':([A-Za-z0-9_]+):', r'：\1：', text)
    return text.translate(_MFM_TRANSLATION)


def to_misskey_text(text: str) -> str:
    """X アカウントはリンクにし、メンションと MFM 装飾を無害化する。

    URL とハッシュタグを保持し、それ以外の MFM の開始記号を全角にする。
    """
    text = re.sub(r'（@([A-Za-z0-9_]+)）', r'（https://x.com/\1）', text)
    parts = []
    end = 0
    for match in _LITERAL_PATTERN.finditer(text):
        parts.append(_neutralize_markup(text[end:match.start()]))
        parts.append(match.group())
        end = match.end()
    parts.append(_neutralize_markup(text[end:]))
    return ''.join(parts)
