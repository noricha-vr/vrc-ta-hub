#!/usr/bin/env python
"""Googleカレンダー同期状況の確認"""

import os
import sys
import django
from datetime import datetime, timedelta

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from django.utils import timezone
from django.db.models import Count, Q
from event.models import Event
from community.models import Community


def check_sync_status():
    """同期状況を確認"""
    
    print("=== Googleカレンダー同期状況 ===\n")
    
    today = timezone.now().date()
    
    # 今後のイベント数を集計
    future_events = Event.objects.filter(date__gte=today)
    total_future = future_events.count()
    
    # Googleカレンダーに同期済みのイベント
    synced_events = future_events.filter(google_calendar_event_id__isnull=False)
    synced_count = synced_events.count()
    
    # 未同期のイベント
    unsynced_events = future_events.filter(google_calendar_event_id__isnull=True)
    unsynced_count = unsynced_events.count()
    
    print(f"今後のイベント総数: {total_future}件")
    print(f"同期済み: {synced_count}件 ({synced_count/total_future*100:.1f}%)")
    print(f"未同期: {unsynced_count}件 ({unsynced_count/total_future*100:.1f}%)")
    
    # コミュニティ別の同期状況
    print("\n=== コミュニティ別同期状況 ===")
    
    communities = Community.objects.filter(
        events__date__gte=today
    ).annotate(
        total_events=Count('events', filter=Q(events__date__gte=today)),
        synced_events=Count('events', filter=Q(
            events__date__gte=today,
            events__google_calendar_event_id__isnull=False
        ))
    ).distinct().order_by('name')
    
    for community in communities:
        if community.total_events > 0:
            sync_rate = community.synced_events / community.total_events * 100
            status = "✓" if sync_rate == 100 else "△" if sync_rate > 0 else "✗"
            print(f"{status} {community.name}: {community.synced_events}/{community.total_events} ({sync_rate:.0f}%)")
    
    # 直近1週間の未同期イベント
    print("\n=== 直近1週間の未同期イベント ===")
    
    one_week_later = today + timedelta(days=7)
    upcoming_unsynced = unsynced_events.filter(
        date__lte=one_week_later
    ).select_related('community').order_by('date', 'start_time')[:10]
    
    if upcoming_unsynced:
        for event in upcoming_unsynced:
            print(f"- {event.date} {event.start_time} {event.community.name}")
    else:
        print("なし（すべて同期済み）")
    
    # 同期エラーの可能性があるイベント（google_calendar_event_idが空文字）
    error_events = future_events.filter(google_calendar_event_id='')
    if error_events.exists():
        print(f"\n⚠️ 同期エラーの可能性があるイベント: {error_events.count()}件")


if __name__ == '__main__':
    check_sync_status()