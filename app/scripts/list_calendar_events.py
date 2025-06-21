#!/usr/bin/env python
import os
import sys
import django
from datetime import datetime, timedelta

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ta_hub.settings')
django.setup()

from event.sync_to_google_v2 import ImprovedDatabaseToGoogleSyncV2
from django.utils import timezone

def list_calendar_events():
    """現在のGoogle Calendarイベントを表示"""
    
    sync = ImprovedDatabaseToGoogleSyncV2()
    
    # 同期期間を設定（過去30日から未来60日）
    start_date = timezone.now().date() - timedelta(days=30)
    end_date = timezone.now().date() + timedelta(days=60)
    
    print(f"Fetching Google Calendar events from {start_date} to {end_date}")
    print("=" * 80)
    
    # Google Calendarイベントを取得
    all_google_events = sync.service.list_events(
        time_min=datetime.combine(start_date, datetime.min.time()),
        time_max=datetime.combine(end_date, datetime.max.time())
    )
    
    print(f"Total events found: {len(all_google_events)}")
    print("=" * 80)
    
    # コミュニティ別にグループ化して重複をチェック
    event_groups = {}
    
    for event in all_google_events:
        summary = event.get('summary', 'No Title')
        start = event.get('start', {})
        
        # 日時を取得
        if 'dateTime' in start:
            dt = datetime.fromisoformat(start['dateTime'].replace('Z', '+00:00'))
            dt = dt.astimezone(timezone.get_current_timezone())
            date_str = dt.strftime('%Y-%m-%d %H:%M')
        elif 'date' in start:
            date_str = start['date']
        else:
            date_str = 'Unknown'
        
        # キーを作成（日時 + サマリー）
        key = f"{date_str}|{summary}"
        
        if key not in event_groups:
            event_groups[key] = []
        
        event_groups[key].append({
            'id': event['id'],
            'summary': summary,
            'date': date_str,
            'description': event.get('description', '')[:50]
        })
    
    # 重複イベントを表示
    print("\n重複イベント（同じ日時・タイトル）:")
    print("-" * 80)
    
    duplicate_count = 0
    for key, events in sorted(event_groups.items()):
        if len(events) > 1:
            duplicate_count += 1
            date_str, summary = key.split('|', 1)
            print(f"\n重複 #{duplicate_count}: {summary}")
            print(f"日時: {date_str}")
            print(f"件数: {len(events)}")
            for i, event in enumerate(events, 1):
                print(f"  {i}. ID: {event['id']}")
    
    if duplicate_count == 0:
        print("重複イベントは見つかりませんでした。")
    else:
        print(f"\n合計 {duplicate_count} 組の重複が見つかりました。")
    
    # 最近のイベントを表示
    print("\n" + "=" * 80)
    print("最近のイベント（10件）:")
    print("-" * 80)
    
    sorted_events = sorted(all_google_events, 
                         key=lambda x: x.get('start', {}).get('dateTime', x.get('start', {}).get('date', '')), 
                         reverse=True)
    
    for i, event in enumerate(sorted_events[:10], 1):
        summary = event.get('summary', 'No Title')
        start = event.get('start', {})
        
        if 'dateTime' in start:
            dt = datetime.fromisoformat(start['dateTime'].replace('Z', '+00:00'))
            dt = dt.astimezone(timezone.get_current_timezone())
            date_str = dt.strftime('%Y-%m-%d %H:%M')
        elif 'date' in start:
            date_str = start['date']
        else:
            date_str = 'Unknown'
        
        print(f"{i}. {summary}")
        print(f"   日時: {date_str}")
        print(f"   ID: {event['id']}")
        print()

if __name__ == "__main__":
    list_calendar_events()