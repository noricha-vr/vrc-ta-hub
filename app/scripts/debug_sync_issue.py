#!/usr/bin/env python
"""同期問題のデバッグ"""

import os
import sys
import django
from datetime import datetime, timedelta

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ta_hub.settings')
django.setup()

from django.conf import settings
from django.utils import timezone
from event.models import Event
from community.models import Community
from event.sync_to_google_v2 import ImprovedDatabaseToGoogleSyncV2

def debug_sync_issue():
    """同期問題をデバッグ"""
    sync = ImprovedDatabaseToGoogleSyncV2()
    
    # 問題が発生している日付（2025-06-20）周辺のイベントを確認
    target_date = datetime(2025, 6, 20).date()
    
    print("=" * 80)
    print("1. データベースのイベントを確認")
    print("=" * 80)
    
    # CS集会、ML集会、エンジニア作業飲み集会のイベントを確認
    target_communities = ["CS集会", "ML集会", "エンジニア作業飲み集会"]
    
    for community_name in target_communities:
        try:
            community = Community.objects.get(name=community_name)
            events = Event.objects.filter(
                community=community,
                date=target_date
            )
            
            print(f"\n{community_name}:")
            for event in events:
                print(f"  - Date: {event.date}, Time: {event.start_time}")
                print(f"    Google ID: {event.google_calendar_event_id}")
                print(f"    DB Key: {sync._create_datetime_key(event.date, event.start_time)}|{event.community.name}")
        except Community.DoesNotExist:
            print(f"\n{community_name}: Community not found")
    
    print("\n" + "=" * 80)
    print("2. Google Calendarのイベントを詳細確認")
    print("=" * 80)
    
    # Google Calendarイベントを取得
    start_date = target_date - timedelta(days=1)
    end_date = target_date + timedelta(days=1)
    
    all_google_events = sync.service.list_events(
        time_min=datetime.combine(start_date, datetime.min.time()),
        time_max=datetime.combine(end_date, datetime.max.time())
    )
    
    print(f"Total Google events: {len(all_google_events)}")
    
    # 問題のあるイベントを詳細に確認
    print("\n2025-06-20のイベント:")
    for event in all_google_events:
        start = event.get('start', {})
        summary = event.get('summary', '')
        
        if 'dateTime' in start:
            dt = datetime.fromisoformat(start['dateTime'].replace('Z', '+00:00'))
            dt = dt.astimezone(timezone.get_current_timezone())
            
            if dt.date() == target_date and summary in target_communities:
                print(f"\nEvent: {summary}")
                print(f"  ID: {event['id']}")
                print(f"  DateTime (raw): {start['dateTime']}")
                print(f"  DateTime (local): {dt}")
                print(f"  Date: {dt.date()}, Time: {dt.time()}")
                
                # インデックスキーを生成
                dt_key = sync._create_datetime_key(dt.date(), dt.time())
                combined_key = f"{dt_key}|{summary}"
                print(f"  Index key: {combined_key}")
    
    print("\n" + "=" * 80)
    print("3. インデックス化の動作を確認")
    print("=" * 80)
    
    # インデックスを作成
    indexed = sync._index_events_by_datetime_and_summary(all_google_events)
    
    print("\nIndexed keys for 2025-06-20:")
    for key in sorted(indexed.keys()):
        if "2025-06-20" in key:
            event = indexed[key]
            print(f"  Key: {key}")
            print(f"    -> Event ID: {event['id']}")
            print(f"    -> Summary: {event.get('summary', 'No summary')}")

if __name__ == '__main__':
    debug_sync_issue()