"""YouTube の URL から動画 ID を取り出す（発表の動画欄の判定はここだけで行う）。

詳細ページの埋め込み（event.views.helpers.extract_video_id）と、記事の自動生成・一覧のサムネイル
（EventDetail.video_id）で判定が食い違わないよう、1 つにまとめている。
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

# 動画 ID を取り出してよいホスト（www. などのサブドメインを含む）。youtube_url には Discord のメッセージリンクも入る
_YOUTUBE_HOSTS = ('youtube.com', 'youtube-nocookie.com')
_SHORT_HOSTS = ('youtu.be',)
# /<種類>/<ID> の形で動画を指すパス。channel・@ハンドル・playlist などは動画ではない
_VIDEO_PATH_KINDS = frozenset({'embed', 'v', 'e', 'live', 'shorts'})
# パスの区切りの先頭にある 11 文字の ID。「youtu.be/<ID>&t=30」「/v/<ID>&hl=ja」のように
# ? ではなく & で続く崩れた URL もあるので、ID の後ろは ID に使わない文字か終わりならよい
_LEADING_VIDEO_ID = re.compile(r'([0-9A-Za-z_-]{11})(?![0-9A-Za-z_-])')
# watch?v=<ID>。「watch?v=<ID>?t=123」のように ? が 2 つある崩れた URL も拾う
_WATCH_QUERY_ID = re.compile(r'(?:^|[&?])v=([0-9A-Za-z_-]{11})(?![0-9A-Za-z_-])')


def _is_host(host: str, allowed_hosts: tuple[str, ...]) -> bool:
    return any(host == allowed or host.endswith(f'.{allowed}') for allowed in allowed_hosts)


def _leading_video_id(segment: str) -> Optional[str]:
    match = _LEADING_VIDEO_ID.match(segment)
    return match.group(1) if match else None


def youtube_video_id(url: Optional[str]) -> Optional[str]:
    """YouTube の動画 URL から 11 文字の動画 ID を返す。動画でない URL・YouTube 以外の URL は None。

    対応: youtu.be/<ID>（www. 付きも）、youtube.com/watch?v=<ID>、/embed/・/v/・/e/・/live/・/shorts/<ID>
    """
    if not url:
        return None
    parsed = urlparse(url if '://' in url else f'https://{url}')
    host = (parsed.hostname or '').lower()
    segments = [segment for segment in parsed.path.split('/') if segment]

    if _is_host(host, _SHORT_HOSTS):
        return _leading_video_id(segments[0]) if segments else None
    if not _is_host(host, _YOUTUBE_HOSTS):
        return None
    if segments[:1] == ['watch']:
        match = _WATCH_QUERY_ID.search(parsed.query)
        return match.group(1) if match else None
    if len(segments) < 2 or segments[0] not in _VIDEO_PATH_KINDS:
        return None
    return _leading_video_id(segments[1])
