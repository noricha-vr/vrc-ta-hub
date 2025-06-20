#!/usr/bin/env python
"""強制的に完全同期を実行"""

import os
import sys
import django
from datetime import datetime, timedelta

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from django.utils import timezone
from event.models import Event
from community.models import Community
from event.sync_to_google import DatabaseToGoogleSync

def force_complete_sync():
    """強制的に完全同期を実行"""
    print("=== 強制完全同期を開始 ===\n")
    
    # まず現在の状態を確認
    today = timezone.now().date()
    db_events = Event.objects.filter(date__gte=today)
    total_db = db_events.count()
    synced_db = db_events.filter(google_calendar_event_id__isnull=False).exclude(google_calendar_event_id='').count()
    
    print(f"データベースの状態:")
    print(f"  総イベント数: {total_db}")
    print(f"  同期済み: {synced_db}")
    print(f"  未同期: {total_db - synced_db}")
    
    if total_db == synced_db:
        print("\nすべてのイベントにGoogle Calendar IDが設定されています")
        print("Googleカレンダー側の同期を確認します...\n")
    
    # Googleカレンダーの現在の状態を確認
    sync = DatabaseToGoogleSync()
    end_date = today + timedelta(days=90)
    
    google_events = sync.service.list_events(
        time_min=datetime.combine(today, datetime.min.time()),
        time_max=datetime.combine(end_date, datetime.max.time())
    )
    
    print(f"\nGoogleカレンダーの状態:")
    print(f"  総イベント数: {len(google_events)}")
    
    # Google Calendar IDのマッピングを確認
    google_id_map = {event['id']: event for event in google_events}
    
    # データベースのイベントを1つずつ確認
    print("\n同期状態の詳細確認:")
    missing_in_google = 0
    
    communities = Community.objects.filter(status='approved').order_by('name')
    
    for community in communities[:5]:  # まず5つのコミュニティで確認
        events = community.events.filter(date__gte=today, date__lte=end_date).order_by('date')
        
        if events.exists():
            print(f"\n{community.name}:")
            print(f"  DBイベント数: {events.count()}")
            
            google_count = 0
            for event in events[:3]:  # 各コミュニティの最初の3イベントを確認
                if event.google_calendar_event_id:
                    if event.google_calendar_event_id in google_id_map:
                        google_count += 1
                    else:
                        print(f"    ✗ {event.date} - Google Calendar IDはあるがGoogleカレンダーに存在しない")
                        missing_in_google += 1
                else:
                    print(f"    ✗ {event.date} - Google Calendar IDが未設定")
            
            # Googleカレンダー側のイベント数を確認
            google_community_events = [e for e in google_events if e.get('summary', '') == community.name]
            print(f"  Googleイベント数: {len(google_community_events)}")
    
    print(f"\n\nGoogleカレンダーに存在しないイベント数: {missing_in_google}")
    
    # 強制同期を実行
    if missing_in_google > 0 or len(google_events) < total_db:
        print("\n=== 強制同期を開始 ===")
        
        # まず、既存のGoogle Calendar IDをクリア（重複を防ぐため）
        print("\n1. 既存のGoogle Calendar IDをクリア")
        cleared = Event.objects.filter(
            date__gte=today,
            google_calendar_event_id__isnull=False
        ).update(google_calendar_event_id=None)
        print(f"  {cleared}件のGoogle Calendar IDをクリアしました")
        
        # Googleカレンダーをクリア
        print("\n2. Googleカレンダーをクリア")
        for event in google_events:
            try:
                sync.service.delete_event(event['id'])
            except:
                pass
        print(f"  {len(google_events)}件のイベントを削除しました")
        
        # 新たに同期
        print("\n3. 新規同期を実行")
        sync.sync_all_communities(months_ahead=3)
        
    else:
        print("\n同期は既に完了しているようです")

if __name__ == '__main__':
    force_complete_sync()