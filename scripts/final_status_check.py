#!/usr/bin/env python
"""最終ステータス確認"""

import os
import sys
import django
from datetime import datetime, timedelta

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from django.utils import timezone
from django.db.models import Count
from event.models import Event
from community.models import Community
from event.sync_to_google import DatabaseToGoogleSync

def final_status_check():
    """最終ステータス確認"""
    print("=== 最終ステータス確認 ===\n")
    
    today = timezone.now().date()
    
    # 1. データベースの状況
    print("1. データベースの状況")
    total_events = Event.objects.filter(date__gte=today).count()
    synced_events = Event.objects.filter(
        date__gte=today,
        google_calendar_event_id__isnull=False
    ).exclude(google_calendar_event_id='').count()
    
    print(f"今日以降のイベント総数: {total_events}件")
    print(f"Google Calendar IDが設定済み: {synced_events}件")
    print(f"未同期: {total_events - synced_events}件")
    
    # 2. Googleカレンダーの状況
    print("\n2. Googleカレンダーの状況")
    sync = DatabaseToGoogleSync()
    end_date = today + timedelta(days=90)
    
    all_events = sync.service.list_events(
        time_min=datetime.combine(today, datetime.min.time()),
        time_max=datetime.combine(end_date, datetime.max.time())
    )
    
    print(f"Googleカレンダーのイベント総数: {len(all_events)}件")
    
    # イベント名でグループ化
    event_summary = {}
    for event in all_events:
        summary = event.get('summary', 'No title')
        if summary not in event_summary:
            event_summary[summary] = 0
        event_summary[summary] += 1
    
    print("\nGoogleカレンダー内のイベント内訳:")
    for summary, count in sorted(event_summary.items()):
        print(f"  - {summary}: {count}件")
    
    # 3. 重複の詳細確認
    print("\n3. 重複の詳細確認")
    
    # データベースの重複
    db_duplicates = Event.objects.filter(
        date__gte=today
    ).values('community__name', 'date', 'start_time').annotate(
        count=Count('id')
    ).filter(count__gt=1)
    
    if db_duplicates:
        print(f"データベースの重複: {len(db_duplicates)}件")
    else:
        print("データベースの重複: なし")
    
    # Googleカレンダーの重複
    google_event_groups = {}
    for event in all_events:
        start_time = event.get('start', {}).get('dateTime', '')
        if 'T' in start_time:
            start_time = start_time.split('T')[0] + 'T' + start_time.split('T')[1][:8]
        
        key = (event.get('summary', ''), start_time)
        if key not in google_event_groups:
            google_event_groups[key] = []
        google_event_groups[key].append(event)
    
    google_duplicates = [(k, v) for k, v in google_event_groups.items() if len(v) > 1]
    
    if google_duplicates:
        print(f"\nGoogleカレンダーの重複: {len(google_duplicates)}件")
        for (summary, start_time), events in google_duplicates:
            print(f"\n{summary} - {start_time}:")
            # 各イベントのデータベース登録状況を確認
            for event in events:
                event_id = event['id']
                db_event = Event.objects.filter(
                    google_calendar_event_id=event_id
                ).first()
                if db_event:
                    print(f"  ✓ {event_id} (DB登録あり: {db_event.community.name})")
                else:
                    print(f"  ✗ {event_id} (DB登録なし)")
    else:
        print("\nGoogleカレンダーの重複: なし")
    
    # 4. 同期プロセスの状況
    print("\n4. 同期プロセスの状況")
    
    # 同期が必要なイベント
    unsync_events = Event.objects.filter(
        date__gte=today,
        google_calendar_event_id__isnull=True
    ).count()
    
    empty_id_events = Event.objects.filter(
        date__gte=today,
        google_calendar_event_id=''
    ).count()
    
    print(f"Google Calendar IDが未設定: {unsync_events}件")
    print(f"Google Calendar IDが空文字: {empty_id_events}件")
    
    if unsync_events + empty_id_events > 0:
        print("\n⚠️ まだ同期が完了していないイベントがあります")
    else:
        print("\n✓ すべてのイベントが同期されています")

if __name__ == '__main__':
    final_status_check()