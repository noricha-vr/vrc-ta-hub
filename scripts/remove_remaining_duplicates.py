#!/usr/bin/env python
"""残りの重複イベントを削除"""

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

def remove_remaining_duplicates():
    """残りの重複イベントを削除"""
    print("=== 残りの重複イベントを削除 ===\n")
    
    sync = DatabaseToGoogleSync()
    today = timezone.now().date()
    end_date = today + timedelta(days=90)
    
    # すべてのイベントを取得
    all_events = sync.service.list_events(
        time_min=datetime.combine(today, datetime.min.time()),
        time_max=datetime.combine(end_date, datetime.max.time())
    )
    
    # イベントをグループ化して重複を検出
    event_groups = {}
    for event in all_events:
        start_time = event.get('start', {}).get('dateTime', '')
        if 'T' in start_time:
            start_time = start_time.split('T')[0] + 'T' + start_time.split('T')[1][:8]
        
        key = (event.get('summary', ''), start_time)
        if key not in event_groups:
            event_groups[key] = []
        event_groups[key].append(event)
    
    # データベースに登録されているGoogle Calendar IDを取得
    valid_ids = set(Event.objects.filter(
        date__gte=today,
        google_calendar_event_id__isnull=False
    ).exclude(
        google_calendar_event_id=''
    ).values_list('google_calendar_event_id', flat=True))
    
    print(f"データベースに登録されているGoogle Calendar ID数: {len(valid_ids)}")
    
    # 重複しているグループから削除対象を選定
    delete_targets = []
    
    for (summary, start_time), events in event_groups.items():
        if len(events) > 1:
            print(f"\n重複グループ: {summary} - {start_time} ({len(events)}件)")
            
            # データベースに登録されているイベントを探す
            valid_event = None
            for event in events:
                if event['id'] in valid_ids:
                    valid_event = event
                    print(f"  ✓ 保持: {event['id']} (DBに登録あり)")
                    break
            
            # 有効なイベントが見つからない場合は最初のものを保持
            if not valid_event:
                valid_event = events[0]
                print(f"  ✓ 保持: {valid_event['id']} (最初のイベント)")
            
            # 残りを削除対象に
            for event in events:
                if event['id'] != valid_event['id']:
                    delete_targets.append(event)
                    print(f"  ✗ 削除対象: {event['id']}")
    
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

if __name__ == '__main__':
    remove_remaining_duplicates()