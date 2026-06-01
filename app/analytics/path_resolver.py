"""GA4 の pagePath を内部コンテンツ（Community / EventDetail）に紐付ける。

GA4 が返す pagePath（例: /community/12/）から対象モデルを引き、権限判定の
基点となる community を解決する。一致しない/対象が存在しないパスは None を返し、
呼び出し側でスキップさせる。
"""
import re

from community.models import Community
from event.models import EventDetail

from .models import PageAnalytics

# クエリ文字列・末尾スラッシュ無しは対象外（正規化済みの正準URLのみ紐付ける）
_COMMUNITY_PATTERN = re.compile(r'^/community/(\d+)/$')
_EVENT_DETAIL_PATTERN = re.compile(r'^/event/detail/(\d+)/$')


def resolve_page_path(page_path: str) -> dict | None:
    """pagePath を Community / EventDetail に紐付ける。

    Args:
        page_path: GA4 が返すページパス（例: /community/12/）。

    Returns:
        紐付け成功時は content_type / community / object_id を含む dict。
        一致しない、または対象が存在しない場合は None。
    """
    if not page_path:
        return None

    community_match = _COMMUNITY_PATTERN.match(page_path)
    if community_match:
        pk = int(community_match.group(1))
        community = Community.objects.filter(pk=pk).first()
        if community is None:
            return None
        return {
            'content_type': PageAnalytics.ContentType.COMMUNITY,
            'community': community,
            'object_id': pk,
        }

    event_detail_match = _EVENT_DETAIL_PATTERN.match(page_path)
    if event_detail_match:
        pk = int(event_detail_match.group(1))
        # community は event 経由で解決する（EventDetail に直接 community FK は無い）
        event_detail = (
            EventDetail.objects.select_related('event__community')
            .filter(pk=pk)
            .first()
        )
        if event_detail is None:
            return None
        return {
            'content_type': PageAnalytics.ContentType.EVENT_DETAIL,
            'community': event_detail.event.community,
            'object_id': pk,
        }

    return None
