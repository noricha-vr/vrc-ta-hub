#!/usr/bin/env python
"""Googleカレンダーのイベントをリスト表示"""

import os
import sys
import django
from datetime import datetime, timedelta

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from event.google_calendar import GoogleCalendarService
from django.conf import settings


def list_calendar_events(days=3):
    """Googleカレンダーから指定日数分のイベントを取得して表示"""
    
    # GoogleCalendarServiceを初期化
    service = GoogleCalendarService(
        calendar_id=settings.GOOGLE_CALENDAR_ID,
        credentials_path=settings.GOOGLE_CALENDAR_CREDENTIALS
    )
    
    # 期間を設定
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    time_min = today.isoformat() + 'Z'
    time_max = (today + timedelta(days=days)).isoformat() + 'Z'
    
    print(f'取得期間: {today.strftime("%Y-%m-%d")} から {days}日間\n')
    
    # Googleカレンダーからイベントを取得
    try:
        events_result = service.service.events().list(
            calendarId=service.calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            maxResults=100,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        print(f'取得したイベント数: {len(events)}件\n')
        
        # 日付ごとにグループ化して表示
        current_date = None
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            summary = event.get('summary', 'No Title')
            event_id = event['id']
            
            # 日付と時刻を抽出
            if 'T' in start:
                event_datetime = datetime.fromisoformat(start.replace('Z', '+00:00'))
                event_date = event_datetime.date()
                event_time = event_datetime.strftime('%H:%M')
            else:
                event_date = datetime.strptime(start, '%Y-%m-%d').date()
                event_time = '終日'
            
            # 日付が変わったら見出しを表示
            if current_date != event_date:
                current_date = event_date
                print(f'\n=== {event_date.strftime("%Y年%m月%d日 (%A)")} ===')
            
            print(f'  {event_time} - {summary}')
            print(f'    ID: {event_id[:8]}...')
            
            # 繰り返しルールがある場合は表示
            if 'recurrence' in event:
                print(f'    繰り返し: {event["recurrence"]}')
            
            # 親イベントIDがある場合は表示
            if 'recurringEventId' in event:
                print(f'    親イベントID: {event["recurringEventId"][:8]}...')
        
    except Exception as e:
        print(f'エラー: {e}')
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    list_calendar_events(3)