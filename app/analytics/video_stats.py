"""発表動画の公開統計の同期と再生数の集計。"""
import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Max, OuterRef, Q, Subquery
from django.utils import timezone

from event.models import EventDetail
from event.youtube_urls import youtube_video_id

from .models import YouTubeVideoStats
from .youtube_client import fetch_video_statistics

logger = logging.getLogger('analytics')


def sync_youtube_stats(target_date) -> int:
    """youtube_url を持つ発表の動画統計を指定日で冪等に保存し、保存件数を返す。

    API が返すのは「今」の累計なので、保存してよいのは前日分の同期の時だけ。
    過去日の再同期（?date= 指定）で当時の行を今の値に上書きしないよう、前日以外は取得しない。
    """
    yesterday = timezone.localdate() - timedelta(days=1)
    if target_date != yesterday:
        logger.info('sync_youtube_stats skipped: date=%s is not yesterday (%s)', target_date, yesterday)
        return 0

    details = (
        EventDetail.objects.filter(youtube_url__isnull=False)
        .exclude(youtube_url='')
        .order_by('pk')
        .values_list('pk', 'youtube_url')
    )
    # 同じ動画の代表は最初の発表。動画 ID 単位で API 取得・保存を重複させない。
    detail_by_video = {}
    for detail_id, url in details:
        video_id = youtube_video_id(url)
        if video_id:
            detail_by_video.setdefault(video_id, detail_id)

    statistics = fetch_video_statistics(list(detail_by_video)) if detail_by_video else {}
    saved = 0
    with transaction.atomic():
        for video_id, detail_id in detail_by_video.items():
            if video_id not in statistics:
                continue
            YouTubeVideoStats.objects.update_or_create(
                video_id=video_id,
                date=target_date,
                defaults={
                    'event_detail_id': detail_id,
                    **statistics[video_id],
                },
            )
            saved += 1

    logger.info(
        'sync_youtube_stats done: date=%s requested=%d fetched=%d saved=%d',
        target_date, len(detail_by_video), len(statistics), saved,
    )
    return saved


def get_video_views(event_detail_ids, *, days=30) -> dict:
    """指定した発表の累計と期間再生数を動画 ID で対応づけて返す。

    累計は最新スナップショット。期間は既存の解析と同じ today-days 〜 前日の days 日分で、
    期間再生数は「前日の累計 − 期間初日の前日の累計」。片端の行・再生数が欠ける時は None。
    呼び出し側で閲覧権限を確認した発表 ID を渡すこと。
    """
    if days < 1:
        raise ValueError('days must be positive')
    if not event_detail_ids:
        return {}

    details = EventDetail.objects.filter(pk__in=event_detail_ids).values_list('pk', 'youtube_url')
    video_by_detail = {}
    for detail_id, url in details:
        video_id = youtube_video_id(url)
        if video_id:
            video_by_detail[detail_id] = video_id
    if not video_by_detail:
        return {}

    today = timezone.localdate()
    # 期間初日の再生分を含めるため、始点は期間初日の前日（today-days-1）の累計にする
    since = today - timedelta(days=days + 1)
    until = today - timedelta(days=1)
    latest = YouTubeVideoStats.objects.filter(video_id=OuterRef('video_id')).order_by('-date')
    rows = (
        YouTubeVideoStats.objects.filter(video_id__in=set(video_by_detail.values()))
        .order_by()
        .values('video_id')
        .annotate(
            total=Subquery(latest.values('view_count')[:1]),
            start=Max('view_count', filter=Q(date=since)),
            end=Max('view_count', filter=Q(date=until)),
        )
    )
    views_by_video = {
        row['video_id']: {
            'total': row['total'],
            'period': max(row['end'] - row['start'], 0)
            if row['start'] is not None and row['end'] is not None else None,
        }
        for row in rows
    }
    return {
        detail_id: views_by_video.get(video_id, {'total': None, 'period': None})
        for detail_id, video_id in video_by_detail.items()
    }


def summarize_video_views(event_detail_ids, *, days=30):
    """発表ごとの再生数と、その合計 (views, total, is_cumulative) を返す。

    同じ動画を複数の発表が使っていても合計には 1 回だけ数える。期間の再生数が出せない動画が
    1 本でもあれば、期間と累計を混ぜないよう全体を累計で合算して is_cumulative=True にする。
    取得できた動画が無ければ total は None。
    """
    views = get_video_views(event_detail_ids, days=days)
    by_video = {}
    for detail_id, url in EventDetail.objects.filter(pk__in=views).values_list('pk', 'youtube_url'):
        row = views[detail_id]
        if row['total'] is not None:
            by_video[youtube_video_id(url)] = row
    if not by_video:
        return views, None, False
    if all(row['period'] is not None for row in by_video.values()):
        return views, sum(row['period'] for row in by_video.values()), False
    return views, sum(row['total'] for row in by_video.values()), True
