"""X 向けの告知本文を安全な Misskey 本文へ変換する。"""

import re


_URL_PATTERN = re.compile(r'(https?://[^\s<>（）「」]+)')


def to_misskey_text(text: str) -> str:
    """X アカウントはリンクにし、メンションと MFM 装飾を無害化する。

    URL 内の @ やハッシュタグは保持し、URL 外の @ は全角にする。
    関数構文とタグの開始文字も全角にして、利用者入力を装飾として解釈させない。
    """
    text = re.sub(r'（@([A-Za-z0-9_]+)）', r'（https://x.com/\1）', text)
    parts = _URL_PATTERN.split(text)
    return ''.join(
        part if index % 2 else part.replace('@', '＠').replace('$[', '＄[').replace('<', '＜').replace('>', '＞')
        for index, part in enumerate(parts)
    )
