#!/usr/bin/env python
"""同期の問題を詳細に分析"""

import os
import sys
import django
from datetime import datetime, timedelta
from collections import defaultdict

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from django.utils import timezone
from event.models import Event
from event.sync_to_google import DatabaseToGoogleSync

def analyze_sync_issue():
    """同期の問題を詳細に分析"""
    print("=== 同期問題の詳細分析 ===\n")
    
    sync = DatabaseToGoogleSync()
    today = timezone.now().date()
    
    # Googleカレンダーの全イベントを取得
    google_events = sync.service.list_events(
        time_min=datetime.combine(today, datetime.min.time()),
        time_max=datetime.combine(today + timedelta(days=7), datetime.max.time())
    )
    
    print(f"Googleカレンダーのイベント数（今後1週間）: {len(google_events)}")
    
    # イベントの詳細を表示
    print("\n## Googleカレンダーのイベント詳細:")
    
    # 日付とコミュニティでグループ化
    event_groups = defaultdict(list)
    
    for event in google_events:
        summary = event.get('summary', 'No title')
        start = event.get('start', {})
        
        if 'dateTime' in start:
            start_datetime = start['dateTime']
            date_str = start_datetime.split('T')[0]
            time_str = start_datetime.split('T')[1][:5]
        else:
            date_str = start.get('date', 'Unknown')
            time_str = 'All day'
        
        key = f"{date_str} {time_str} - {summary}"
        
        event_info = {
            'id': event['id'],
            'summary': summary,
            'date': date_str,
            'time': time_str,
            'created': event.get('created', 'Unknown'),
            'updated': event.get('updated', 'Unknown'),
            'recurringEventId': event.get('recurringEventId'),
            'recurrence': event.get('recurrence')
        }
        
        event_groups[key].append(event_info)
    
    # グループごとに表示
    for key, events in sorted(event_groups.items()):
        if len(events) > 1:
            print(f"\n【重複】{key} ({len(events)}件):")
        else:
            print(f"\n{key}:")
        
        for event in events:
            print(f"  ID: {event['id']}")
            print(f"  作成日時: {event['created']}")
            print(f"  更新日時: {event['updated']}")
            if event['recurringEventId']:
                print(f"  繰り返しイベントID: {event['recurringEventId']}")
            if event['recurrence']:
                print(f"  繰り返しルール: {event['recurrence']}")
    
    # データベースとの照合
    print("\n\n## データベースとの照合:")
    
    # Google Calendar IDのマッピング
    db_events = Event.objects.filter(
        date__gte=today,
        date__lte=today + timedelta(days=7),
        google_calendar_event_id__isnull=False
    ).exclude(google_calendar_event_id='')
    
    db_google_ids = set(db_events.values_list('google_calendar_event_id', flat=True))
    google_ids = set(event['id'] for event in google_events)
    
    print(f"\nデータベースに登録されているGoogle Calendar ID数: {len(db_google_ids)}")
    print(f"Googleカレンダーのイベント数: {len(google_ids)}")
    
    # DBにあってGoogleにないID
    missing_in_google = db_google_ids - google_ids
    if missing_in_google:
        print(f"\n⚠️ DBにあるがGoogleカレンダーにないID: {len(missing_in_google)}件")
        for gid in list(missing_in_google)[:5]:
            event = Event.objects.get(google_calendar_event_id=gid)
            print(f"  {gid}: {event.community.name} - {event.date}")
    
    # GoogleにあってDBにないID
    missing_in_db = google_ids - db_google_ids
    if missing_in_db:
        print(f"\n⚠️ GoogleカレンダーにあるがDBにないID: {len(missing_in_db)}件")
        for gid in missing_in_db:
            for event in google_events:
                if event['id'] == gid:
                    print(f"  {gid}: {event.get('summary', 'No title')}")
                    break
    
    # 同じイベントで異なるGoogle Calendar IDを持つものがあるか確認
    print("\n\n## 同一イベントの重複登録チェック:")
    
    # コミュニティ、日付、時刻でグループ化
    db_event_groups = defaultdict(list)
    for event in Event.objects.filter(date__gte=today, date__lte=today + timedelta(days=7)):
        key = f"{event.community.name}_{event.date}_{event.start_time}"
        db_event_groups[key].append(event)
    
    duplicate_db_events = [(k, v) for k, v in db_event_groups.items() if len(v) > 1]
    
    if duplicate_db_events:
        print(f"\n⚠️ データベースに重複イベントがあります: {len(duplicate_db_events)}件")
        for key, events in duplicate_db_events[:5]:
            print(f"\n{key}:")
            for event in events:
                print(f"  ID: {event.id}, Google Calendar ID: {event.google_calendar_event_id}")
    else:
        print("\nデータベースに重複イベントはありません")

if __name__ == '__main__':
    analyze_sync_issue()