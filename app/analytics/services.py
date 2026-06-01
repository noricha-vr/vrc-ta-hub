"""ページ解析の集計サービス（権限境界の単一ゲート）。

全ての集計クエリは accessible_community_ids が返す community_ids で必ず絞る。
object_id だけで絞ると他 community のデータが混入するため、community__in を
省略してはならない。
"""
import logging
from datetime import date, timedelta

from django.db.models import Sum
from django.utils import timezone

from community.models import Community
from event.models import EventDetail

from .models import PageAnalytics

logger = logging.getLogger('analytics')

DEFAULT_DAYS = 30
# ダッシュボード「人気記事ランキング」のデフォルト件数
TOP_EVENT_DETAILS_LIMIT = 50


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


def get_overall_stats(community_ids, *, days=DEFAULT_DAYS) -> dict:
    """指定期間の合計 PV/UU/Sessions と、直前同期間との比較率を返す。

    Args:
        community_ids: アクセス可能な community id（必須の権限境界）。
        days: 遡る日数。

    Returns:
        {
            'pv', 'users', 'sessions': 現在期間の合計（int）
            'pv_prev', 'users_prev', 'sessions_prev': 直前同日数の合計（int）
            'pv_change_pct', 'users_change_pct', 'sessions_change_pct': 変化率%（float, 前期間が0なら None）
        }
    """
    today = timezone.localdate()
    current_start = today - timedelta(days=days)
    prev_start = current_start - timedelta(days=days)

    # community_ids が空なら集計しても 0 件。早期 return で DB を叩かない
    if not community_ids:
        return _empty_overall_stats()

    current = (
        PageAnalytics.objects
        .filter(community_id__in=community_ids, date__gte=current_start)
        .aggregate(pv=Sum('pv'), users=Sum('users'), sessions=Sum('sessions'))
    )
    prev = (
        PageAnalytics.objects
        .filter(
            community_id__in=community_ids,
            date__gte=prev_start,
            date__lt=current_start,
        )
        .aggregate(pv=Sum('pv'), users=Sum('users'), sessions=Sum('sessions'))
    )

    def _change(curr, before):
        if before in (None, 0):
            return None
        return round((curr - before) / before * 100, 1)

    pv_curr = current['pv'] or 0
    users_curr = current['users'] or 0
    sessions_curr = current['sessions'] or 0
    pv_prev = prev['pv'] or 0
    users_prev = prev['users'] or 0
    sessions_prev = prev['sessions'] or 0

    return {
        'pv': pv_curr,
        'users': users_curr,
        'sessions': sessions_curr,
        'pv_prev': pv_prev,
        'users_prev': users_prev,
        'sessions_prev': sessions_prev,
        'pv_change_pct': _change(pv_curr, pv_prev),
        'users_change_pct': _change(users_curr, users_prev),
        'sessions_change_pct': _change(sessions_curr, sessions_prev),
    }


def _empty_overall_stats() -> dict:
    """community_ids が空のとき返す ゼロ埋めの統計（テンプレで if 分岐を減らすため）。"""
    return {
        'pv': 0, 'users': 0, 'sessions': 0,
        'pv_prev': 0, 'users_prev': 0, 'sessions_prev': 0,
        'pv_change_pct': None, 'users_change_pct': None, 'sessions_change_pct': None,
    }


def get_event_detail_breakdown(
    community_ids, *, days=DEFAULT_DAYS, limit=TOP_EVENT_DETAILS_LIMIT,
) -> list:
    """記事（EventDetail）別の PV/UU/Sessions 合計と主要流入元を返す。

    EventDetail.id で集計し、テンプレで使うメタ情報（タイトル / 公開日 / URL slug）と
    主要流入元の上位1件を埋め込む。テンプレで `|dictsort` 可能な dict のリストにする。

    Args:
        community_ids: アクセス可能な community id（必須の権限境界）。
        days: 遡る日数。
        limit: 返す件数の上限（PV降順）。

    Returns:
        各要素は {
            'event_detail_id', 'theme', 'published_at', 'community_name',
            'pv', 'users', 'sessions', 'top_source',
        }
    """
    if not community_ids:
        return []

    # まず PV 降順で event_detail 単位に集計
    base = _base_queryset(
        community_ids,
        content_type=PageAnalytics.ContentType.EVENT_DETAIL,
        days=days,
    )
    aggregates = list(
        base.values('object_id')
        .annotate(pv=Sum('pv'), users=Sum('users'), sessions=Sum('sessions'))
        .order_by('-pv')[:limit]
    )

    if not aggregates:
        return []

    event_detail_ids = [row['object_id'] for row in aggregates]

    # EventDetail を bulk fetch（N+1 を避ける）
    event_details = (
        EventDetail.objects
        .select_related('event__community')
        .filter(pk__in=event_detail_ids)
    )
    detail_map = {ed.pk: ed for ed in event_details}

    # 各 event_detail の流入元上位1件
    top_sources = (
        base.filter(object_id__in=event_detail_ids)
        .values('object_id', 'source_medium')
        .annotate(pv=Sum('pv'))
        .order_by('object_id', '-pv')
    )
    top_source_map = {}
    for row in top_sources:
        # 同じ object_id 内で最初に来たものが PV 最大（order_by の優先順）
        top_source_map.setdefault(row['object_id'], row['source_medium'])

    results = []
    for row in aggregates:
        pk = row['object_id']
        ed = detail_map.get(pk)
        if ed is None:
            # 紐付けが切れた（記事削除等）レコードは無視
            continue
        results.append({
            'event_detail_id': pk,
            'theme': ed.theme or ed.h1 or '(無題)',
            'published_at': ed.event.date,
            'community_name': ed.event.community.name,
            'pv': row['pv'] or 0,
            'users': row['users'] or 0,
            'sessions': row['sessions'] or 0,
            'top_source': top_source_map.get(pk, '(不明)'),
        })

    return results


def get_post_publish_series(community_ids, *, days_after=14, top_n=5) -> dict:
    """公開後の経過日数を揃えた PV 推移を、人気上位 N 記事について返す。

    各記事の公開日（event.date）から N 日間のPVを「経過日数: 0,1,2,...」で並べる。
    異なる記事を1つの折れ線チャートに重ねて表示するためのデータ。

    Args:
        community_ids: アクセス可能な community id（必須の権限境界）。
        days_after: 公開日から何日後まで集計するか。
        top_n: 上位何記事を返すか。

    Returns:
        {
            'labels': ['Day 0', 'Day 1', ..., f'Day {days_after}'],
            'datasets': [
                {'label': テーマ, 'data': [pv_day0, pv_day1, ...]},
                ...
            ],
        }
    """
    if not community_ids:
        return {'labels': [], 'datasets': []}

    # 候補選定: 全期間で PV 上位を選ぶ（公開からの経過期間に関係なく、人気記事を網羅）。
    # 直近期間で絞ると古い人気記事が落ち、その後の Day 0〜N PV 取得時に
    # 「窓内にデータなし」で全件 0 になりチャートが空になる不整合が出るため
    top_records = (
        PageAnalytics.objects
        .filter(
            community_id__in=community_ids,
            content_type=PageAnalytics.ContentType.EVENT_DETAIL,
        )
        .values('object_id')
        .annotate(pv=Sum('pv'))
        .order_by('-pv')[:top_n]
    )
    event_detail_ids = [row['object_id'] for row in top_records]
    if not event_detail_ids:
        return {'labels': [], 'datasets': []}

    event_details = (
        EventDetail.objects
        .select_related('event')
        .filter(pk__in=event_detail_ids)
    )
    detail_map = {ed.pk: ed for ed in event_details}

    labels = [f'Day {i}' for i in range(days_after + 1)]
    datasets = []

    for ed_id in event_detail_ids:
        ed = detail_map.get(ed_id)
        if ed is None:
            continue
        publish_date: date = ed.event.date

        # 公開日基準で日付範囲を絶対指定して PV を取得（権限境界の community_id__in は維持）
        day_pvs = {
            row['date']: row['pv']
            for row in (
                PageAnalytics.objects.filter(
                    community_id__in=community_ids,
                    content_type=PageAnalytics.ContentType.EVENT_DETAIL,
                    object_id=ed_id,
                    date__gte=publish_date,
                    date__lte=publish_date + timedelta(days=days_after),
                )
                .values('date')
                .annotate(pv=Sum('pv'))
            )
        }
        data = [
            day_pvs.get(publish_date + timedelta(days=i), 0) or 0
            for i in range(days_after + 1)
        ]
        # 経過日のうちすべて 0 なら省略（チャートが汚れるため）
        if not any(data):
            continue
        datasets.append({
            'label': (ed.theme or ed.h1 or '(無題)')[:30],
            'data': data,
        })

    return {'labels': labels, 'datasets': datasets}
