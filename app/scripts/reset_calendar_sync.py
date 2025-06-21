#!/usr/bin/env python
"""Googleカレンダーの今日以降のイベントを削除して再同期"""

import os
import sys
import django
from datetime import datetime, timedelta

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from django.conf import settings
from event.google_calendar import GoogleCalendarService
from community.models import Community
from event.models import Event


def clear_and_resync():
    """Googleカレンダーをクリアして再同期"""
    calendar_id = settings.GOOGLE_CALENDAR_ID
    credentials_path = settings.GOOGLE_CALENDAR_CREDENTIALS
    service = GoogleCalendarService(calendar_id, credentials_path=credentials_path)
    
    print('=== Googleカレンダーのクリアと再同期 ===')
    print(f'カレンダーID: {calendar_id}')
    
    # 1. 今日以降のGoogleカレンダーイベントを削除
    print('\n1. 今日以降のGoogleカレンダーイベントを削除中...')
    
    today = datetime.now().date()
    time_min = datetime.combine(today, datetime.min.time())
    time_max = time_min + timedelta(days=365)  # 1年先まで
    
    try:
        # Googleカレンダーからイベントを取得
        google_events = service.service.events().list(
            calendarId=calendar_id,
            timeMin=time_min.isoformat() + 'Z',
            timeMax=time_max.isoformat() + 'Z',
            singleEvents=True,
            orderBy='startTime',
            maxResults=2500
        ).execute()
        
        items = google_events.get('items', [])
        print(f'  削除対象イベント数: {len(items)}件')
        
        # 各イベントを削除
        deleted_count = 0
        for event in items:
            try:
                service.delete_event(event['id'])
                deleted_count += 1
                if deleted_count % 10 == 0:
                    print(f'  {deleted_count}件削除...')
            except Exception as e:
                print(f'  削除エラー: {event.get("summary", "Unknown")}: {e}')
        
        print(f'  → {deleted_count}件のイベントを削除しました')
        
    except Exception as e:
        print(f'  エラー: Googleカレンダーからのイベント取得に失敗: {e}')
        return
    
    # 2. DBからイベントを再登録
    print('\n2. データベースからイベントを再登録中...')
    
    # 同期期間（1ヶ月先まで）
    end_date = today + timedelta(days=30)
    
    # アクティブなコミュニティを取得
    communities = Community.objects.filter(is_active=True)
    total_synced = 0
    
    for community in communities:
        # 期間内のイベントを取得（マスターイベントは除外）
        events = Event.objects.filter(
            community=community,
            date__gte=today,
            date__lte=end_date,
            is_recurring_master=False
        ).order_by('date', 'start_time')
        
        if events.exists():
            print(f'\n  {community.name} (ID: {community.id})')
            sync_count = 0
            
            for event in events:
                try:
                    # Googleカレンダーにイベントを作成
                    google_event_id = service.create_or_update_event(event)
                    if google_event_id:
                        # DBにGoogleカレンダーIDを保存
                        event.google_calendar_event_id = google_event_id
                        event.save(update_fields=['google_calendar_event_id'])
                        sync_count += 1
                except Exception as e:
                    print(f'    エラー: {event.date} {event.start_time}: {e}')
            
            print(f'    → {sync_count}件同期')
            total_synced += sync_count
    
    print(f'\n=== 完了 ===')
    print(f'削除: {deleted_count}件')
    print(f'登録: {total_synced}件')


if __name__ == '__main__':
    clear_and_resync()