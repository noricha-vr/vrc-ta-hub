"""Host 関連の正規化ユーティリティ。"""

import os
from urllib.parse import urlsplit


def normalize_host(value: str | None) -> str:
    """URL/host どちらで渡されても host 部分だけを取り出す。"""
    if not value:
        return ''

    candidate = value.strip().rstrip('/')
    if '://' not in candidate:
        candidate = f'//{candidate}'

    parsed = urlsplit(candidate)
    if parsed.hostname:
        return parsed.hostname

    return parsed.netloc.split('/')[0]


def get_canonical_host() -> str:
    return normalize_host(os.environ.get('APP_CANONICAL_HOST', 'vrc-ta-hub.com'))
