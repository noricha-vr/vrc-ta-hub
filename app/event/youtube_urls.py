"""YouTube の URL から動画 ID を取り出す（発表の動画欄の判定はここだけで行う）。

詳細ページの埋め込み（event.views.helpers.extract_video_id）と、記事の自動生成・一覧のサムネイル
（EventDetail.video_id）で判定が食い違わないよう、1 つにまとめている。
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

# 動画 ID を取り出してよいホスト（サブドメインを含む）。youtube_url には Discord のメッセージリンクも入る
_YOUTUBE_HOSTS = ('youtube.com', 'youtube-nocookie.com')
_SHORT_HOST = 'youtu.be'
# /<種類>/<ID> の形で動画を指すパス。channel・@ハンドル・playlist などは動画ではない
_VIDEO_PATH_KINDS = frozenset({'embed', 'v', 'e', 'live', 'shorts'})
_VIDEO_ID = re.compile(r'[0-9A-Za-z_-]{11}')
# watch?v=<ID>。「watch?v=<ID>?t=123」のように ? が 2 つある崩れた URL も拾う
_WATCH_QUERY_ID = re.compile(r'(?:^|[&?])v=([0-9A-Za-z_-]{11})(?![0-9A-Za-z_-])')


def youtube_video_id(url: Optional[str]) -> Optional[str]:
    """YouTube の動画 URL から 11 文字の動画 ID を返す。動画でない URL・YouTube 以外の URL は None。

    対応: youtu.be/<ID>、youtube.com/watch?v=<ID>、/embed/・/v/・/e/・/live/・/shorts/<ID>
    """
    if not url:
        return None
    parsed = urlparse(url if '://' in url else f'https://{url}')
    host = (parsed.hostname or '').lower()
    segments = [segment for segment in parsed.path.split('/') if segment]

    if host == _SHORT_HOST:
        candidate = segments[0] if segments else ''
    elif any(host == allowed or host.endswith(f'.{allowed}') for allowed in _YOUTUBE_HOSTS):
        if segments[:1] == ['watch']:
            match = _WATCH_QUERY_ID.search(parsed.query)
            return match.group(1) if match else None
        if len(segments) < 2 or segments[0] not in _VIDEO_PATH_KINDS:
            return None
        candidate = segments[1]
    else:
        return None
    return candidate if _VIDEO_ID.fullmatch(candidate) else None
