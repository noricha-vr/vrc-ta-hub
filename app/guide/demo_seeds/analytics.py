"""アクセス解析と配布リンクの架空の集計。撮影時だけ共通デモに追加する。"""
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile

from analytics.models import (
    Campaign, PageAnalytics, PosterClick, SearchConsoleQuery, YouTubeVideoAnalytics, YouTubeVideoStats,
)
from analytics.qr_generator import generate_qr_png
from event.models import EventDetail
from tests.factories import make_event, make_event_detail


def seed(context):
    if not getattr(settings, 'GUIDE_CAPTURE_EXTENDED_DEMO', False):
        return
    community = context['community']
    past = make_event(community, event_date=context['base_date'] - timedelta(days=7))
    detail = make_event_detail(
        past, applicant=context['users']['speaker'], speaker='アオイ', theme='小さなワールドの作り方',
        status='approved',
    )
    # 保存シグナルによる資料公開通知を呼ばず、撮影専用の状態を置く。
    EventDetail.objects.filter(pk=detail.pk).update(
        youtube_url='https://www.youtube.com/watch?v=guideDemo01',
        contents='## ワールド制作の入口\n小さな部屋から作ってみましょう。',
        slide_url='https://example.com/slides/world', h1='小さなワールドの作り方',
    )
    for offset in (1, 8, 31, 91):
        YouTubeVideoStats.objects.create(
            video_id='guideDemo01', event_detail=detail,
            date=context['base_date'] - timedelta(days=offset), view_count=1000 - offset * 5,
        )
    campaign = Campaign.objects.create(
        community=community, name='春の案内ポスター', utm_source='poster', utm_medium='qr',
        utm_campaign='demo-spring-2030', landing_path=f'/community/{community.pk}/',
        distributed_at=context['base_date'], note='架空の配布例です。',
    )
    png = generate_qr_png(campaign.url)
    campaign.qr_image.save('demo.png', ContentFile(png.read()), save=True)
    for offset in range(1, 61):
        day = context['base_date'] - timedelta(days=offset)
        YouTubeVideoAnalytics.objects.create(
            video_id='guideDemo01', event_detail=detail, date=day, views=5,
            estimated_minutes_watched=15, average_view_duration_seconds=180,
        )
        for kind, obj, path, pv in [
            ('community', community, f'/community/{community.pk}/', 12),
            ('event_detail', detail, f'/event/detail/{detail.pk}/', 8),
        ]:
            for source, multiplier in [('google / organic', 2), ('twitter / social', 1)]:
                PageAnalytics.objects.create(
                    community=community, content_type=kind, object_id=obj.pk, page_path=path,
                    date=day, source_medium=source, pv=pv * multiplier + offset % 3,
                    users=pv, sessions=pv + 1,
                )
        PosterClick.objects.create(community=community, date=day, clicks=6, users=4)
    for query, clicks, impressions in [('VRChat 技術 集会', 24, 420), ('ワールド制作 勉強会', 12, 180),
                                       ('さくら技術交流会', 8, 100)]:
        SearchConsoleQuery.objects.create(
            community=community, date=context['base_date'] - timedelta(days=3),
            page_path=f'/community/{community.pk}/', query=query, clicks=clicks,
            impressions=impressions, position=4.2, content_type='community', object_id=community.pk,
        )
    PageAnalytics.objects.create(
        community=community, date=context['base_date'] - timedelta(days=2),
        page_path=f'/community/{community.pk}/', content_type='community', object_id=community.pk,
        source_medium='poster / qr', campaign=campaign.utm_campaign, pv=36, users=28, sessions=30,
    )
