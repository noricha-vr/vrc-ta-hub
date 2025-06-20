#!/usr/bin/env python
"""Googleカレンダーの重複イベントを調査"""

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
from event.google_calendar import GoogleCalendarService
from event.models import Event
from django.conf import settings


def investigate_duplicates():
    """Googleカレンダーの重複を調査"""
    
    print("=== Googleカレンダー重複調査 ===\n")
    
    # GoogleCalendarServiceを初期化
    service = GoogleCalendarService(
        calendar_id=settings.GOOGLE_CALENDAR_ID,
        credentials_path=settings.GOOGLE_CALENDAR_CREDENTIALS
    )
    
    # 今日の日付を設定
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    time_min = today.isoformat() + 'Z'
    time_max = (today + timedelta(days=7)).isoformat() + 'Z'
    
    print(f"調査期間: {today.strftime('%Y-%m-%d')} から 7日間\n")
    
    # Googleカレンダーからイベントを取得
    try:
        events_result = service.service.events().list(
            calendarId=service.calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            maxResults=500,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        google_events = events_result.get('items', [])
        print(f"Googleカレンダーのイベント数: {len(google_events)}件\n")
        
        # イベントを日付・時刻・タイトルでグループ化
        event_groups = defaultdict(list)
        
        for event in google_events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            summary = event.get('summary', 'No Title')
            
            # 日付と時刻を抽出
            if 'T' in start:
                event_datetime = datetime.fromisoformat(start.replace('Z', '+00:00'))
                key = (event_datetime.date(), event_datetime.time(), summary)
            else:
                key = (start, None, summary)
            
            event_groups[key].append(event)
        
        # 重複を検出
        print("=== 重複イベント ===")
        duplicate_count = 0
        
        for key, events in event_groups.items():
            if len(events) > 1:
                date, time, title = key
                duplicate_count += 1
                print(f"\n重複 #{duplicate_count}:")
                print(f"  日付: {date}")
                print(f"  時刻: {time}")
                print(f"  タイトル: {title}")
                print(f"  件数: {len(events)}件")
                
                # 各イベントの詳細
                for i, event in enumerate(events):
                    print(f"\n  イベント {i+1}:")
                    print(f"    ID: {event['id']}")
                    print(f"    作成日時: {event.get('created', 'N/A')}")
                    print(f"    更新日時: {event.get('updated', 'N/A')}")
                    
                    # 繰り返しルールの確認
                    if 'recurrence' in event:
                        print(f"    繰り返し: {event['recurrence']}")
                    else:
                        print(f"    繰り返し: なし")
                    
                    # recurringEventIdの確認
                    if 'recurringEventId' in event:
                        print(f"    親イベントID: {event['recurringEventId']}")
        
        if duplicate_count == 0:
            print("重複は見つかりませんでした")
        else:
            print(f"\n合計 {duplicate_count} 組の重複が見つかりました")
        
        # データベースとの照合
        print("\n\n=== データベースとの照合 ===")
        
        # 同期間のデータベースイベントを取得
        db_events = Event.objects.filter(
            date__gte=today.date(),
            date__lt=(today + timedelta(days=7)).date()
        ).select_related('community')
        
        print(f"データベースのイベント数: {db_events.count()}件")
        
        # Google Calendar IDでグループ化
        google_id_count = defaultdict(int)
        for event in db_events:
            if event.google_calendar_event_id:
                google_id_count[event.google_calendar_event_id] += 1
        
        # 重複IDを検出
        duplicate_ids = {gid: count for gid, count in google_id_count.items() if count > 1}
        
        if duplicate_ids:
            print(f"\n同じGoogle Calendar IDを持つイベント:")
            for gid, count in duplicate_ids.items():
                print(f"  {gid}: {count}件")
        
    except Exception as e:
        print(f"エラー: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    investigate_duplicates()