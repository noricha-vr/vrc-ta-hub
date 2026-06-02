"""GA4 の pagePath を内部コンテンツ（Community / EventDetail / Campaign）に紐付ける。

GA4 が返す pagePath（例: /community/12/）から対象モデルを引き、権限判定の
基点となる community を解決する。pagePath で解決できない場合は utm_campaign 経由で
Campaign テーブルを逆引きする（landing_path=/ のチラシ流入を取りこぼさないため）。
"""
import re

from community.models import Community
from event.models import EventDetail

from .models import Campaign, PageAnalytics

# クエリ文字列・末尾スラッシュ無しは対象外（正規化済みの正準URLのみ紐付ける）
_COMMUNITY_PATTERN = re.compile(r'^/community/(\d+)/$')
_EVENT_DETAIL_PATTERN = re.compile(r'^/event/detail/(\d+)/$')

# GA4 が utm_campaign 未指定セッションに付ける標準ラベル。これは Campaign 解決対象外
_CAMPAIGN_NOT_SET = '(not set)'


def resolve_page_path(page_path: str, campaign: str | None = None) -> dict | None:
    """pagePath を Community / EventDetail / Campaign に紐付ける。

    解決優先順位は (1) pagePath → Community / EventDetail (2) utm_campaign → Campaign。
    pagePath が `/` などコンテンツ非依存でも、主催者が発行した Campaign で
    集計対象に取り込めるようにする。

    Args:
        page_path: GA4 が返すページパス（例: /community/12/）。
        campaign: GA4 が返す utm_campaign（任意）。`(not set)` や None は無視する。

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

    # pagePath で解決できない場合、utm_campaign で Campaign を逆引きする。
    # Campaign.Meta.unique_together = ('community', 'utm_campaign') のため
    # 同一 utm_campaign が複数 community に存在しうる。曖昧な場合は GLOBAL 扱いに倒す
    # （誤った community への割り当てによる集計汚染を避ける Fail Safe）。
    if campaign and campaign != _CAMPAIGN_NOT_SET:
        matches = list(
            Campaign.objects.filter(utm_campaign=campaign)
            .select_related('community')[:2]
        )
        if len(matches) == 1:
            c = matches[0]
            return {
                'content_type': PageAnalytics.ContentType.CAMPAIGN,
                'community': c.community,
                'object_id': c.pk,
            }

    return None
