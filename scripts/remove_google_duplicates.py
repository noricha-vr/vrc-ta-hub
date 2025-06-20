#!/usr/bin/env python
"""Googleカレンダーの重複イベントを削除"""

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
from event.sync_to_google import DatabaseToGoogleSync

def remove_google_duplicates():
    """Googleカレンダーの重複イベントを削除"""
    print("=== Googleカレンダーの重複削除 ===\n")
    
    sync = DatabaseToGoogleSync()
    today = timezone.now().date()
    end_date = today + timedelta(days=90)
    
    # すべてのイベントを取得
    all_events = sync.service.list_events(
        time_min=datetime.combine(today, datetime.min.time()),
        time_max=datetime.combine(end_date, datetime.max.time())
    )
    
    # データベースに登録されているGoogle Calendar IDを取得
    valid_ids = set(Event.objects.filter(
        date__gte=today,
        google_calendar_event_id__isnull=False
    ).exclude(
        google_calendar_event_id=''
    ).values_list('google_calendar_event_id', flat=True))
    
    print(f"データベースに登録されているGoogle Calendar ID数: {len(valid_ids)}")
    
    # 削除対象のイベントを特定
    delete_targets = []
    
    # 重複している特定のイベントID
    duplicate_ids = [
        '0c03u5onoq9cecba0fv3bnf47o_20250621T120000Z',
        'cikd3pbbbkgfd8cj01cnib31a4',
        'if02cp9rgmjgkn06nt8cl5edro',
        '2j038h99s66f89f0802ne3f7os_20250621T120000Z',
        'j5kp7h9ghupbo3e86l77h5vo74_20250621T120000Z',
        'lk9lv62fbdl1ln10valvobr298'
    ]
    
    for event in all_events:
        event_id = event['id']
        
        # 重複IDリストにあり、かつデータベースに登録されていないイベントを削除対象とする
        if event_id in duplicate_ids and event_id not in valid_ids:
            delete_targets.append(event)
            print(f"削除対象: {event.get('summary', 'No title')} - {event_id}")
    
    # 削除実行
    if delete_targets:
        print(f"\n{len(delete_targets)}件のイベントを削除します...")
        
        deleted_count = 0
        for event in delete_targets:
            try:
                sync.service.delete_event(event['id'])
                deleted_count += 1
                print(f"✓ 削除完了: {event['id']}")
            except Exception as e:
                print(f"✗ 削除エラー: {event['id']} - {e}")
        
        print(f"\n✓ {deleted_count}件のイベントを削除しました")
    else:
        print("\n削除対象のイベントはありません")
    
    # 再度重複チェック
    print("\n=== 削除後の重複チェック ===")
    
    all_events = sync.service.list_events(
        time_min=datetime.combine(today, datetime.min.time()),
        time_max=datetime.combine(end_date, datetime.max.time())
    )
    
    # イベントをグループ化
    google_events = {}
    for event in all_events:
        start_time = event.get('start', {}).get('dateTime', '')
        if 'T' in start_time:
            start_time = start_time.split('T')[0] + 'T' + start_time.split('T')[1][:8]
        
        key = (event.get('summary', ''), start_time)
        if key in google_events:
            google_events[key].append(event)
        else:
            google_events[key] = [event]
    
    google_duplicates = [(k, v) for k, v in google_events.items() if len(v) > 1]
    
    if google_duplicates:
        print(f"⚠️ まだ{len(google_duplicates)}件の重複があります")
    else:
        print("✓ 重複はすべて解消されました")

if __name__ == '__main__':
    remove_google_duplicates()