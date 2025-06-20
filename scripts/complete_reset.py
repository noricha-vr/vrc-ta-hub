#!/usr/bin/env python
"""完全リセットして単一プロセスで同期"""

import os
import sys
import django
from datetime import datetime, timedelta
import time

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from django.utils import timezone
from django.db import transaction
from event.models import Event
from event.google_calendar import GoogleCalendarService
from community.models import Community

def complete_reset():
    """完全リセットして単一プロセスで同期"""
    print("=== 完全リセットと同期 ===\n")
    
    # GoogleCalendarServiceを直接使用
    from website.settings import GOOGLE_CALENDAR_ID, GOOGLE_CALENDAR_CREDENTIALS
    service = GoogleCalendarService(
        calendar_id=GOOGLE_CALENDAR_ID,
        credentials_path=GOOGLE_CALENDAR_CREDENTIALS
    )
    
    today = timezone.now().date()
    
    # 1. Googleカレンダーを空にする
    print("1. Googleカレンダーを完全に空にします...")
    
    # maxResultsを使って小刻みに取得して削除
    page_token = None
    total_deleted = 0
    
    while True:
        try:
            if page_token:
                events_result = service.service.events().list(
                    calendarId=service.calendar_id,
                    pageToken=page_token,
                    maxResults=100
                ).execute()
            else:
                events_result = service.service.events().list(
                    calendarId=service.calendar_id,
                    maxResults=100
                ).execute()
            
            events = events_result.get('items', [])
            
            for event in events:
                try:
                    service.service.events().delete(
                        calendarId=service.calendar_id,
                        eventId=event['id']
                    ).execute()
                    total_deleted += 1
                except:
                    pass
            
            page_token = events_result.get('nextPageToken')
            if not page_token:
                break
                
        except Exception as e:
            print(f"   エラー: {e}")
            break
    
    print(f"   {total_deleted}件のイベントを削除しました")
    
    # 2. データベースをクリア
    print("\n2. データベースのGoogle Calendar IDをクリア...")
    with transaction.atomic():
        Event.objects.update(google_calendar_event_id=None)
    print("   完了")
    
    # 3. Google APIの処理完了を待つ
    print("\n3. API処理の完了を待機（10秒）...")
    time.sleep(10)
    
    # 4. 確認：Googleカレンダーが空であることを確認
    print("\n4. Googleカレンダーが空であることを確認...")
    check_events = service.list_events(
        time_min=datetime(2024, 1, 1),
        time_max=datetime(2026, 12, 31)
    )
    
    if check_events:
        print(f"   ⚠️ まだ{len(check_events)}件のイベントが残っています")
        for event in check_events:
            try:
                service.delete_event(event['id'])
            except:
                pass
        print("   追加削除を実行しました")
    else:
        print("   ✓ Googleカレンダーは空です")
    
    # 5. 単一プロセスで同期
    print("\n5. 単一プロセスで同期を開始...")
    
    end_date = today + timedelta(days=90)
    
    # すべてのイベントを取得
    all_events = Event.objects.filter(
        date__gte=today,
        date__lte=end_date
    ).select_related('community').order_by('date', 'start_time')
    
    print(f"   同期対象: {all_events.count()}件")
    
    created_count = 0
    error_count = 0
    
    # バッチ処理ではなく1件ずつ処理
    for i, event in enumerate(all_events):
        if i % 20 == 0:
            print(f"   進捗: {i}/{all_events.count()}")
            # APIレート制限を考慮
            time.sleep(1)
        
        try:
            # イベントデータを準備
            start_datetime = datetime.combine(event.date, event.start_time)
            end_datetime = start_datetime + timedelta(minutes=event.duration)
            
            # タイムゾーン設定
            tz = timezone.get_current_timezone()
            start_datetime = timezone.make_aware(start_datetime, tz)
            end_datetime = timezone.make_aware(end_datetime, tz)
            
            # 説明文
            description = f"集会: {event.community.name}\\n"
            description += f"開催日時: {event.date.strftime('%Y年%m月%d日')} {event.start_time.strftime('%H:%M')}\\n"
            description += f"開催時間: {event.duration}分"
            
            # Googleカレンダーに作成（繰り返しなし）
            result = service.create_event(
                summary=event.community.name,
                start_time=start_datetime,
                end_time=end_datetime,
                description=description,
                recurrence=None
            )
            
            # データベースを更新
            event.google_calendar_event_id = result['id']
            event.save()
            
            created_count += 1
            
        except Exception as e:
            error_count += 1
            print(f"   エラー ({event.community.name} - {event.date}): {e}")
    
    print(f"\n   同期完了: 作成 {created_count}件, エラー {error_count}件")
    
    # 6. 最終確認
    print("\n6. 最終確認...")
    time.sleep(5)
    
    final_events = service.list_events(
        time_min=datetime.combine(today, datetime.min.time()),
        time_max=datetime.combine(end_date, datetime.max.time())
    )
    
    print(f"\nGoogleカレンダーのイベント数: {len(final_events)}")
    
    # 重複チェック
    seen = set()
    duplicates = []
    
    for event in final_events:
        key = f"{event.get('summary', '')}|{event.get('start', {}).get('dateTime', '')[:16]}"
        if key in seen:
            duplicates.append(key)
        else:
            seen.add(key)
    
    if duplicates:
        print(f"\n⚠️ 重複検出: {len(duplicates)}件")
        for dup in duplicates[:10]:
            print(f"  {dup}")
    else:
        print("\n✅ 重複なし！")
    
    # データベース確認
    synced = Event.objects.filter(
        date__gte=today,
        date__lte=end_date,
        google_calendar_event_id__isnull=False
    ).exclude(google_calendar_event_id='').count()
    
    print(f"\nデータベースの同期済みイベント: {synced}件")
    
    print("\n=== 処理完了 ===")

if __name__ == '__main__':
    complete_reset()