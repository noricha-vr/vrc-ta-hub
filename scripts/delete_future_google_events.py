#!/usr/bin/env python
"""Googleカレンダーの今日以降のイベントを削除"""

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


def delete_future_events():
    """今日以降のGoogleカレンダーイベントを削除"""
    calendar_id = settings.GOOGLE_CALENDAR_ID
    credentials_path = settings.GOOGLE_CALENDAR_CREDENTIALS
    service = GoogleCalendarService(calendar_id, credentials_path=credentials_path)
    
    print('=== Googleカレンダーの今日以降のイベントを削除 ===')
    print(f'カレンダーID: {calendar_id}')
    
    today = datetime.now().date()
    time_min = datetime.combine(today, datetime.min.time())
    time_max = time_min + timedelta(days=365)
    
    try:
        # バッチ削除用のリクエストを準備
        batch = service.service.new_batch_http_request()
        
        # イベントを取得
        page_token = None
        total_deleted = 0
        
        while True:
            events = service.service.events().list(
                calendarId=calendar_id,
                timeMin=time_min.isoformat() + 'Z',
                timeMax=time_max.isoformat() + 'Z',
                pageToken=page_token,
                singleEvents=True,
                maxResults=250
            ).execute()
            
            items = events.get('items', [])
            print(f'\n取得したイベント数: {len(items)}件')
            
            # バッチ削除
            for event in items:
                batch.add(service.service.events().delete(
                    calendarId=calendar_id,
                    eventId=event['id']
                ))
            
            if len(items) > 0:
                batch.execute()
                total_deleted += len(items)
                print(f'削除完了: {total_deleted}件')
                
                # 新しいバッチを作成
                batch = service.service.new_batch_http_request()
            
            # 次のページがあるか確認
            page_token = events.get('nextPageToken')
            if not page_token:
                break
        
        print(f'\n=== 完了 ===')
        print(f'合計 {total_deleted}件のイベントを削除しました')
        
    except Exception as e:
        print(f'エラー: {e}')
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    delete_future_events()