#!/usr/bin/env python
"""
Googleカレンダーの1ヶ月以上先のイベントを確認するスクリプト
"""
import os
import sys
import django
from datetime import datetime, timedelta
from django.utils import timezone

# Djangoの設定
sys.path.append('/opt/project/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from event.google_calendar import GoogleCalendarService
from website.settings import GOOGLE_CALENDAR_ID, GOOGLE_CALENDAR_CREDENTIALS, DEBUG


def check_google_calendar_future_events():
    """Googleカレンダーの1ヶ月以上先のイベントを確認"""
    now = timezone.now()
    one_month_later = now + timedelta(days=30)
    two_months_later = now + timedelta(days=60)
    
    print(f"現在の日時: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"1ヶ月後の日時: {one_month_later.strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 50)
    
    try:
        calendar_service = GoogleCalendarService(
            calendar_id=GOOGLE_CALENDAR_ID,
            credentials_path=GOOGLE_CALENDAR_CREDENTIALS if DEBUG else None
        )
        
        # 1ヶ月後から2ヶ月後までのイベントを取得
        events = calendar_service.list_events(
            max_results=100,
            time_min=one_month_later,
            time_max=two_months_later
        )
        
        print(f"\nGoogleカレンダーの1ヶ月以上先のイベント数: {len(events)}件")
        
        if events:
            print("\nイベント一覧:")
            print("-" * 80)
            for event in events:
                start = event.get('start', {})
                start_time = start.get('dateTime', start.get('date'))
                summary = event.get('summary', '(タイトルなし)')
                event_id = event.get('id', '(IDなし)')
                
                print(f"ID: {event_id[:20]}... | {start_time} | {summary}")
        
        # 全体の統計情報も取得
        # 今日から1ヶ月後まで
        current_month_events = calendar_service.list_events(
            max_results=100,
            time_min=now,
            time_max=one_month_later
        )
        
        print(f"\n統計情報:")
        print(f"今日から1ヶ月以内のイベント数: {len(current_month_events)}件")
        print(f"1ヶ月以上先のイベント数: {len(events)}件")
        
    except Exception as e:
        print(f"\nGoogleカレンダーAPIエラー: {e}")


if __name__ == '__main__':
    check_google_calendar_future_events()