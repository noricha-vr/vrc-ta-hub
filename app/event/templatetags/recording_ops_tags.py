"""録画管理画面向けの記事ステータス・権限連携テンプレートタグ。"""
from __future__ import annotations

from typing import Any

from django import template

from community.models import CommunityMember
from event.models import EventDetail
from event.services.recording_ops.dashboard import ArticleStatusSummary, resolve_article_status

register = template.Library()


@register.simple_tag
def get_article_status(detail: EventDetail, request: Any = None) -> ArticleStatusSummary:
    """EventDetail の記事ステータスと遷移リンクを解決する（リクエスト内で管理集会をメモ化）。"""
    user = getattr(request, "user", None)
    managed_ids: set[int] | None = None
    if user is not None and getattr(user, "is_authenticated", False) and not getattr(user, "is_superuser", False):
        cached = getattr(request, "_recording_ops_managed_community_ids", None)
        if cached is None:
            cached = set(
                CommunityMember.objects.filter(user=user).values_list("community_id", flat=True)
            )
            request._recording_ops_managed_community_ids = cached
        managed_ids = cached
    return resolve_article_status(detail, user=user, managed_community_ids=managed_ids)
