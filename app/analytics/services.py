"""ページ解析の集計サービス（権限境界の単一ゲート）。

全ての集計クエリは accessible_community_ids が返す community_ids で必ず絞る。
object_id だけで絞ると他 community のデータが混入するため、community__in を
省略してはならない。
"""
import logging
from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone

from community.models import Community

from .models import PageAnalytics

logger = logging.getLogger('analytics')

DEFAULT_DAYS = 30


def accessible_community_ids(user) -> list[int]:
    """ユーザーがアクセス可能な community の id リストを返す。

    superuser は全 Community、それ以外は所属する CommunityMember
    （owner / staff 両方）の community のみ。未ログイン / AnonymousUser は空。

    Args:
        user: リクエストユーザー（CustomUser または AnonymousUser）。

    Returns:
        アクセス可能な community id のリスト。
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return []

    if user.is_superuser:
        return list(Community.objects.values_list('id', flat=True))

    # owner / staff の区別なく、所属 community すべてにアクセス可
    return list(
        user.community_memberships.values_list('community_id', flat=True)
    )


def _base_queryset(community_ids, *, content_type=None, object_id=None, days=DEFAULT_DAYS):
    """community_ids で必ず絞った PageAnalytics の基底クエリセットを作る。"""
    since = timezone.localdate() - timedelta(days=days)
    # community__in による絞り込みは権限境界。絶対に外さないこと
    queryset = PageAnalytics.objects.filter(
        community_id__in=community_ids,
        date__gte=since,
    )
    if content_type is not None:
        queryset = queryset.filter(content_type=content_type)
    if object_id is not None:
        queryset = queryset.filter(object_id=object_id)
    return queryset


def get_daily_series(community_ids, *, content_type=None, object_id=None, days=DEFAULT_DAYS) -> list:
    """日付別の pv / users / sessions 合計を昇順で返す。

    Args:
        community_ids: アクセス可能な community id（必須の権限境界）。
        content_type: 絞り込むコンテンツ種別（任意）。
        object_id: 絞り込む対象オブジェクトID（任意）。
        days: 遡る日数。

    Returns:
        {'date', 'pv', 'users', 'sessions'} の dict のリスト（date 昇順）。
    """
    return list(
        _base_queryset(
            community_ids, content_type=content_type, object_id=object_id, days=days
        )
        .values('date')
        .annotate(
            pv=Sum('pv'),
            users=Sum('users'),
            sessions=Sum('sessions'),
        )
        .order_by('date')
    )


def get_source_breakdown(community_ids, *, content_type=None, object_id=None, days=DEFAULT_DAYS) -> list:
    """参照元/メディア別の pv 合計を降順で返す。

    Args:
        community_ids: アクセス可能な community id（必須の権限境界）。
        content_type: 絞り込むコンテンツ種別（任意）。
        object_id: 絞り込む対象オブジェクトID（任意）。
        days: 遡る日数。

    Returns:
        {'source_medium', 'pv'} の dict のリスト（pv 降順）。
    """
    return list(
        _base_queryset(
            community_ids, content_type=content_type, object_id=object_id, days=days
        )
        .values('source_medium')
        .annotate(pv=Sum('pv'))
        .order_by('-pv')
    )
