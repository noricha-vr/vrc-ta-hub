#!/usr/bin/env python
import os
import sys
import django
from datetime import datetime, timedelta
from collections import defaultdict

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ta_hub.settings')
django.setup()

from event.sync_to_google_v2 import ImprovedDatabaseToGoogleSyncV2
from django.utils import timezone

def remove_duplicate_calendar_events(dry_run=True):
    """Google Calendarの重複イベントを削除"""
    
    sync = ImprovedDatabaseToGoogleSyncV2()
    
    # 過去30日から未来90日のイベントを取得
    start_date = timezone.now().date() - timedelta(days=30)
    end_date = timezone.now().date() + timedelta(days=90)
    
    print(f"Fetching Google Calendar events from {start_date} to {end_date}")
    print("=" * 80)
    
    # Google Calendarイベントを取得
    all_google_events = sync.service.list_events(
        time_min=datetime.combine(start_date, datetime.min.time()),
        time_max=datetime.combine(end_date, datetime.max.time()),
        max_results=2500
    )
    
    print(f"Total events found: {len(all_google_events)}")
    
    # イベントをグループ化（日時+サマリーでグループ化）
    event_groups = defaultdict(list)
    
    for event in all_google_events:
        summary = event.get('summary', 'No Title')
        start = event.get('start', {})
        
        # 日時を取得
        if 'dateTime' in start:
            dt = datetime.fromisoformat(start['dateTime'].replace('Z', '+00:00'))
            dt = dt.astimezone(timezone.get_current_timezone())
            date_str = dt.strftime('%Y-%m-%d %H:%M')
        elif 'date' in start:
            date_str = start['date']
        else:
            continue
        
        # キーを作成（日時 + サマリー）
        key = f"{date_str}|{summary}"
        event_groups[key].append(event)
    
    # 重複イベントを処理
    print("\n重複イベントの処理:")
    print("-" * 80)
    
    total_deleted = 0
    
    for key, events in sorted(event_groups.items()):
        if len(events) > 1:
            date_str, summary = key.split('|', 1)
            print(f"\n重複: {summary}")
            print(f"日時: {date_str}")
            print(f"件数: {len(events)}")
            
            # 作成日時でソート（新しい順）
            sorted_events = sorted(events, 
                                 key=lambda x: x.get('created', ''), 
                                 reverse=True)
            
            # 最新のイベントを残し、他を削除
            keep_event = sorted_events[0]
            delete_events = sorted_events[1:]
            
            print(f"  保持: ID={keep_event['id']} (created: {keep_event.get('created', 'Unknown')})")
            
            for event in delete_events:
                print(f"  削除: ID={event['id']} (created: {event.get('created', 'Unknown')})")
                
                if not dry_run:
                    try:
                        sync.service.delete_event(event['id'])
                        total_deleted += 1
                        print(f"    -> 削除完了")
                    except Exception as e:
                        print(f"    -> 削除エラー: {e}")
                else:
                    print(f"    -> [DRY RUN] 実際には削除されません")
                    total_deleted += 1
    
    print("\n" + "=" * 80)
    if dry_run:
        print(f"[DRY RUN] {total_deleted}件のイベントが削除対象です。")
        print("実際に削除するには、dry_run=False で実行してください。")
    else:
        print(f"{total_deleted}件のイベントを削除しました。")

if __name__ == "__main__":
    import sys
    
    # コマンドライン引数で --execute を指定した場合は実際に削除
    dry_run = "--execute" not in sys.argv
    
    if not dry_run:
        print("警告: 実際にGoogle Calendarのイベントを削除します。")
        print("削除を開始します...")
    
    remove_duplicate_calendar_events(dry_run=dry_run)