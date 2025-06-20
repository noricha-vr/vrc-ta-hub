#!/usr/bin/env python
"""曜日が間違っているイベントを削除"""

import os
import sys
import django
from datetime import datetime

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from django.db import transaction
from event.models import Event
from event.google_calendar import GoogleCalendarService
from django.conf import settings


def delete_wrong_weekday_events():
    """曜日が間違っているイベントを削除"""
    
    today = datetime.now().date()
    print(f"今日の日付: {today} ({today.strftime('%A')})")
    print("="*50)
    
    # 削除すべきイベントを特定
    events_to_delete = []
    
    events = Event.objects.filter(date=today).select_related('community', 'recurring_master__recurrence_rule')
    
    for event in events:
        should_delete = False
        reason = ""
        
        if event.recurring_master and event.recurring_master.recurrence_rule:
            rule = event.recurring_master.recurrence_rule
            
            if rule.start_date:
                expected_weekday = rule.start_date.weekday()
                actual_weekday = event.date.weekday()
                
                if expected_weekday != actual_weekday:
                    should_delete = True
                    reason = f"曜日不一致: {rule.start_date.strftime('%A')}であるべき"
        
        # 一部の特殊なイベントは今日限りのイベント（単発イベント）
        elif event.community.name in ['VRC MED J SALON', 'VRアカデミア集会', 'VR研究Cafe', 
                                      '分散システム集会', '天文仮想研究所VSP', '昆虫集会',
                                      'アバター改変なんもわからん集会', 'VR酔い訓練集会', 
                                      'VRC競プロ部(ABC感想会)']:
            # これらは単発イベントなので削除しない
            pass
        
        if should_delete:
            events_to_delete.append((event, reason))
    
    print(f"\n削除すべきイベント: {len(events_to_delete)}件\n")
    
    for event, reason in events_to_delete:
        print(f"{event.community.name}")
        print(f"  理由: {reason}")
        print(f"  Event ID: {event.id}")
        print(f"  Google Calendar ID: {event.google_calendar_event_id}")
        print()
    
    # 削除実行
    if events_to_delete:
        print("\n削除を実行しますか？ (Docker環境では自動的にyes)")
        response = 'yes'
        print(response)
        
        if response == 'yes':
            # GoogleCalendarServiceを初期化
            service = GoogleCalendarService(
                calendar_id=settings.GOOGLE_CALENDAR_ID,
                credentials_path=settings.GOOGLE_CALENDAR_CREDENTIALS
            )
            
            with transaction.atomic():
                db_delete_count = 0
                gc_delete_count = 0
                gc_error_count = 0
                
                for event, _ in events_to_delete:
                    # Googleカレンダーから削除
                    if event.google_calendar_event_id:
                        try:
                            service.service.events().delete(
                                calendarId=service.calendar_id,
                                eventId=event.google_calendar_event_id
                            ).execute()
                            gc_delete_count += 1
                        except Exception as e:
                            print(f"  Googleカレンダー削除エラー: {event.community.name} - {e}")
                            gc_error_count += 1
                    
                    # データベースから削除
                    event.delete()
                    db_delete_count += 1
                
                print(f"\n削除完了:")
                print(f"  データベース: {db_delete_count}件")
                print(f"  Googleカレンダー: {gc_delete_count}件")
                if gc_error_count > 0:
                    print(f"  Googleカレンダーエラー: {gc_error_count}件")


if __name__ == '__main__':
    delete_wrong_weekday_events()