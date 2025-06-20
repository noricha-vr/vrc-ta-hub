#!/usr/bin/env python
"""DBからGoogleカレンダーへイベントを同期"""

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


def sync_to_calendar():
    """DBからGoogleカレンダーへ同期"""
    calendar_id = settings.GOOGLE_CALENDAR_ID
    credentials_path = settings.GOOGLE_CALENDAR_CREDENTIALS
    service = GoogleCalendarService(calendar_id, credentials_path=credentials_path)
    
    print('=== DBからGoogleカレンダーへの同期 ===')
    print(f'カレンダーID: {calendar_id}')
    
    # 同期期間（今日から1ヶ月先まで）
    today = datetime.now().date()
    end_date = today + timedelta(days=30)
    
    print(f'同期期間: {today} から {end_date} まで')
    print('='*60)
    
    # アクティブなコミュニティを取得（statusが'approved'のもの）
    communities = Community.objects.filter(status='approved').order_by('name')
    total_synced = 0
    total_errors = 0
    
    for community in communities:
        # 期間内のイベントを取得（マスターイベントは除外）
        events = Event.objects.filter(
            community=community,
            date__gte=today,
            date__lte=end_date,
            is_recurring_master=False
        ).order_by('date', 'start_time')
        
        if events.exists():
            print(f'\n{community.name} (ID: {community.id})')
            print(f'  開催曜日: {", ".join(community.weekdays) if community.weekdays else "不定期"}')
            print(f'  イベント数: {events.count()}件')
            
            sync_count = 0
            error_count = 0
            
            for event in events:
                try:
                    # イベントの開始・終了時刻を作成
                    start_datetime = datetime.combine(event.date, event.start_time)
                    end_datetime = start_datetime + timedelta(minutes=event.duration)
                    
                    # Googleカレンダーにイベントを作成
                    if event.google_calendar_event_id:
                        # 既存のイベントを更新
                        result = service.update_event(
                            event_id=event.google_calendar_event_id,
                            summary=f"{event.community.name}",
                            start_time=start_datetime,
                            end_time=end_datetime,
                            description=f"集会: {event.community.name}\n開催時間: {event.duration}分"
                        )
                    else:
                        # 新規イベントを作成
                        result = service.create_event(
                            summary=f"{event.community.name}",
                            start_time=start_datetime,
                            end_time=end_datetime,
                            description=f"集会: {event.community.name}\n開催時間: {event.duration}分"
                        )
                        # DBにGoogleカレンダーIDを保存
                        if result and 'id' in result:
                            event.google_calendar_event_id = result['id']
                            event.save(update_fields=['google_calendar_event_id'])
                    
                    sync_count += 1
                    print(f'    ✓ {event.date} {event.start_time}')
                except Exception as e:
                    error_count += 1
                    print(f'    ✗ {event.date} {event.start_time}: {e}')
            
            print(f'  → 成功: {sync_count}件, エラー: {error_count}件')
            total_synced += sync_count
            total_errors += error_count
    
    print(f'\n=== 同期完了 ===')
    print(f'成功: {total_synced}件')
    print(f'エラー: {total_errors}件')
    print(f'合計: {total_synced + total_errors}件')


if __name__ == '__main__':
    sync_to_calendar()