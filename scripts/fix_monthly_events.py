#!/usr/bin/env python
"""月次イベントの修正"""

import os
import sys
import django
from datetime import datetime, timedelta

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from django.db import transaction
from event.models import Event
from event.google_calendar import GoogleCalendarService
from django.conf import settings


def fix_monthly_events():
    """月次イベントを修正"""
    
    today = datetime.now().date()
    print(f"今日の日付: {today} ({today.strftime('%A')})")
    print("="*50)
    
    # 削除すべき月次イベント
    wrong_events = Event.objects.filter(
        date=today,
        recurring_master__recurrence_rule__frequency='MONTHLY_BY_WEEK'
    ).select_related('community', 'recurring_master__recurrence_rule')
    
    print("\n削除するイベント:")
    events_to_delete = []
    
    for event in wrong_events:
        rule = event.recurring_master.recurrence_rule
        if rule.start_date:
            # 曜日が異なる場合は削除対象
            if event.date.weekday() != rule.start_date.weekday():
                print(f"- {event.community.name} (ID: {event.id})")
                events_to_delete.append(event)
    
    print(f"\n合計 {len(events_to_delete)}件を削除します")
    
    if events_to_delete:
        # GoogleCalendarServiceを初期化
        service = GoogleCalendarService(
            calendar_id=settings.GOOGLE_CALENDAR_ID,
            credentials_path=settings.GOOGLE_CALENDAR_CREDENTIALS
        )
        
        with transaction.atomic():
            for event in events_to_delete:
                # Googleカレンダーから削除
                if event.google_calendar_event_id:
                    try:
                        service.service.events().delete(
                            calendarId=service.calendar_id,
                            eventId=event.google_calendar_event_id
                        ).execute()
                    except:
                        pass
                
                # データベースから削除
                event.delete()
            
            print("削除完了")
    
    # 今日のイベントを再表示
    print("\n\n今日のイベント（修正後）:")
    print("="*50)
    
    events = Event.objects.filter(date=today).select_related('community').order_by('start_time')
    
    for event in events:
        print(f"{event.start_time} - {event.community.name}")
        if event.recurring_master and event.recurring_master.recurrence_rule:
            rule = event.recurring_master.recurrence_rule
            print(f"  頻度: {rule.frequency}")


if __name__ == '__main__':
    fix_monthly_events()