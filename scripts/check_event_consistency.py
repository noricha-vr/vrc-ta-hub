#!/usr/bin/env python
"""イベントの重複と開催曜日・周期の整合性を確認"""

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
from event.models import Event, RecurrenceRule
from community.models import Community
from event.google_calendar import GoogleCalendarService
from django.conf import settings


def check_event_consistency():
    """イベントの整合性を確認"""
    
    print("=== イベント整合性チェック ===\n")
    
    today = timezone.now().date()
    one_month_later = today + timedelta(days=30)
    
    # 1. データベース内の重複確認
    print("1. データベース内の重複確認")
    print("-" * 40)
    
    events = Event.objects.filter(
        date__gte=today,
        date__lte=one_month_later
    ).select_related('community').order_by('date', 'start_time')
    
    # 重複チェック
    event_dict = defaultdict(list)
    for event in events:
        key = (event.date, event.start_time, event.community.id)
        event_dict[key].append(event)
    
    duplicate_count = 0
    for key, event_list in event_dict.items():
        if len(event_list) > 1:
            duplicate_count += 1
            date, time, community_id = key
            community = event_list[0].community
            print(f"重複: {date} {time} - {community.name}")
            for event in event_list:
                print(f"  ID: {event.id}")
    
    if duplicate_count == 0:
        print("データベース内に重複はありません")
    else:
        print(f"\n{duplicate_count}組の重複が見つかりました")
    
    # 2. 開催曜日と周期の確認
    print("\n\n2. 開催曜日と周期の確認")
    print("-" * 40)
    
    # コミュニティごとにイベントをグループ化
    community_events = defaultdict(list)
    for event in events:
        community_events[event.community].append(event)
    
    inconsistency_count = 0
    
    for community, event_list in community_events.items():
        # RecurrenceRuleを取得
        master_event = Event.objects.filter(
            community=community,
            is_recurring_master=True
        ).select_related('recurrence_rule').first()
        
        if not master_event or not master_event.recurrence_rule:
            continue
        
        rule = master_event.recurrence_rule
        errors = []
        
        # 週次イベントの曜日チェック
        if rule.frequency == 'WEEKLY' and rule.start_date:
            expected_weekday = rule.start_date.weekday()
            
            for event in event_list:
                actual_weekday = event.date.weekday()
                if actual_weekday != expected_weekday:
                    errors.append(f"  {event.date} ({event.date.strftime('%A')}) - 期待: {rule.start_date.strftime('%A')}")
        
        # 週次イベントの間隔チェック
        if rule.frequency == 'WEEKLY' and len(event_list) > 1:
            sorted_events = sorted(event_list, key=lambda x: x.date)
            for i in range(1, len(sorted_events)):
                days_diff = (sorted_events[i].date - sorted_events[i-1].date).days
                expected_diff = rule.interval * 7
                
                if days_diff != expected_diff and days_diff != 0:  # 同日の別時刻は許可
                    errors.append(f"  間隔エラー: {sorted_events[i-1].date} → {sorted_events[i].date} ({days_diff}日, 期待: {expected_diff}日)")
        
        if errors:
            inconsistency_count += 1
            print(f"\n{community.name} (ID: {community.id})")
            print(f"  ルール: {rule.frequency}, interval={rule.interval}, start_date={rule.start_date}")
            print("  問題:")
            for error in errors:
                print(error)
    
    if inconsistency_count == 0:
        print("すべてのイベントが正しい曜日・周期で登録されています")
    
    # 3. Googleカレンダーとの同期状況
    print("\n\n3. Googleカレンダーとの同期状況")
    print("-" * 40)
    
    # 同期されていないイベントを確認
    unsynced_events = Event.objects.filter(
        date__gte=today,
        date__lte=one_month_later,
        google_calendar_event_id__isnull=True
    ).count()
    
    synced_events = Event.objects.filter(
        date__gte=today,
        date__lte=one_month_later,
        google_calendar_event_id__isnull=False
    ).count()
    
    total_events = unsynced_events + synced_events
    
    print(f"総イベント数: {total_events}")
    print(f"同期済み: {synced_events} ({synced_events/total_events*100:.1f}%)")
    print(f"未同期: {unsynced_events}")
    
    # 4. Googleカレンダーの重複確認
    print("\n\n4. Googleカレンダー内の重複確認")
    print("-" * 40)
    
    service = GoogleCalendarService(
        calendar_id=settings.GOOGLE_CALENDAR_ID,
        credentials_path=settings.GOOGLE_CALENDAR_CREDENTIALS
    )
    
    time_min = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat() + 'Z'
    time_max = (datetime.now() + timedelta(days=30)).isoformat() + 'Z'
    
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
        
        # 重複チェック
        google_event_dict = defaultdict(list)
        for event in google_events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            summary = event.get('summary', 'No Title')
            
            if 'T' in start:
                event_datetime = datetime.fromisoformat(start.replace('Z', '+00:00'))
                key = (event_datetime.date(), event_datetime.time(), summary)
            else:
                key = (start, None, summary)
            
            google_event_dict[key].append(event)
        
        google_duplicate_count = 0
        for key, event_list in google_event_dict.items():
            if len(event_list) > 1:
                google_duplicate_count += 1
                date, time, title = key
                print(f"重複: {date} {time} - {title} ({len(event_list)}件)")
        
        if google_duplicate_count == 0:
            print("Googleカレンダー内に重複はありません")
        else:
            print(f"\n{google_duplicate_count}組の重複が見つかりました")
        
    except Exception as e:
        print(f"エラー: {e}")
    
    print("\n=== チェック完了 ===")


if __name__ == '__main__':
    check_event_consistency()