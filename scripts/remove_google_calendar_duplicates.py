#!/usr/bin/env python
"""Googleカレンダーの重複イベントを削除"""

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


def remove_duplicates():
    """Googleカレンダーの重複を削除"""
    
    print("=== Googleカレンダー重複削除 ===\n")
    
    # GoogleCalendarServiceを初期化
    service = GoogleCalendarService(
        calendar_id=settings.GOOGLE_CALENDAR_ID,
        credentials_path=settings.GOOGLE_CALENDAR_CREDENTIALS
    )
    
    # 今日から3ヶ月先までの期間を設定
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    time_min = today.isoformat() + 'Z'
    time_max = (today + timedelta(days=90)).isoformat() + 'Z'
    
    print(f"処理期間: {today.strftime('%Y-%m-%d')} から 90日間\n")
    
    # Googleカレンダーからイベントを取得
    try:
        events_result = service.service.events().list(
            calendarId=service.calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            maxResults=2500,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        google_events = events_result.get('items', [])
        print(f"Googleカレンダーのイベント数: {len(google_events)}件\n")
        
        # データベースのイベントを取得
        db_events = Event.objects.filter(
            date__gte=today.date(),
            date__lt=(today + timedelta(days=90)).date(),
            google_calendar_event_id__isnull=False
        ).select_related('community')
        
        # データベースに登録されているGoogle Calendar IDのセット
        db_google_ids = set(event.google_calendar_event_id for event in db_events)
        print(f"データベースに登録されているGoogle Calendar ID数: {len(db_google_ids)}件\n")
        
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
        
        # 削除するイベントのリスト
        events_to_delete = []
        
        print("=== 重複イベントの処理 ===")
        duplicate_count = 0
        
        for key, events in event_groups.items():
            if len(events) > 1:
                date, time, title = key
                duplicate_count += 1
                
                # データベースに登録されているIDを優先的に残す
                db_registered = [e for e in events if e['id'] in db_google_ids]
                not_registered = [e for e in events if e['id'] not in db_google_ids]
                
                if db_registered:
                    # データベースに登録されているものがある場合、それを残して他を削除
                    keep_event = db_registered[0]  # 最初のものを残す
                    delete_events = [e for e in events if e['id'] != keep_event['id']]
                else:
                    # すべてデータベースに未登録の場合、最も古いものを残す
                    events_sorted = sorted(events, key=lambda x: x.get('created', ''))
                    keep_event = events_sorted[0]
                    delete_events = events_sorted[1:]
                
                print(f"\n重複 #{duplicate_count}: {title}")
                print(f"  日付: {date}, 時刻: {time}")
                print(f"  残すイベント: {keep_event['id']} (作成: {keep_event.get('created', 'N/A')})")
                print(f"  削除予定: {len(delete_events)}件")
                
                events_to_delete.extend(delete_events)
        
        # 削除実行
        if events_to_delete:
            print(f"\n\n削除を実行しますか？ {len(events_to_delete)}件のイベントが削除されます。")
            print("続行する場合は 'yes' と入力してください: ", end='')
            
            # Docker環境では自動的に yes とする
            response = 'yes'
            print(response)
            
            if response.lower() == 'yes':
                deleted_count = 0
                error_count = 0
                
                for event in events_to_delete:
                    try:
                        service.service.events().delete(
                            calendarId=service.calendar_id,
                            eventId=event['id']
                        ).execute()
                        deleted_count += 1
                        
                        # 進捗表示
                        if deleted_count % 10 == 0:
                            print(f"  削除中... {deleted_count}/{len(events_to_delete)}")
                        
                    except Exception as e:
                        error_count += 1
                        print(f"  エラー: {event['id']} - {e}")
                
                print(f"\n=== 削除完了 ===")
                print(f"削除成功: {deleted_count}件")
                print(f"エラー: {error_count}件")
            else:
                print("削除をキャンセルしました")
        else:
            print("\n重複は見つかりませんでした")
        
    except Exception as e:
        print(f"エラー: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    remove_duplicates()