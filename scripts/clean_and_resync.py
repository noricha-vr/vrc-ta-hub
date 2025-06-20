#!/usr/bin/env python
"""Googleカレンダーを完全にクリアして再同期"""

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

def clean_and_resync():
    """Googleカレンダーをクリアして再同期"""
    print("=== Googleカレンダーのクリアと再同期 ===\n")
    
    sync = DatabaseToGoogleSync()
    today = timezone.now().date()
    end_date = today + timedelta(days=365)  # 1年先まで
    
    # 1. Googleカレンダーの全イベントを取得
    print("1. Googleカレンダーの全イベントを取得中...")
    all_events = sync.service.list_events(
        time_min=datetime.combine(today - timedelta(days=30), datetime.min.time()),
        time_max=datetime.combine(end_date, datetime.max.time())
    )
    
    print(f"   取得したイベント数: {len(all_events)}")
    
    # 2. すべてのイベントを削除
    print("\n2. Googleカレンダーのイベントを削除中...")
    deleted_count = 0
    for event in all_events:
        try:
            sync.service.delete_event(event['id'])
            deleted_count += 1
            if deleted_count % 50 == 0:
                print(f"   {deleted_count}件削除済み...")
        except Exception as e:
            print(f"   削除エラー: {event.get('summary', 'Unknown')} - {e}")
    
    print(f"   削除完了: {deleted_count}件")
    
    # 3. データベースのGoogle Calendar IDをクリア
    print("\n3. データベースのGoogle Calendar IDをクリア中...")
    cleared = Event.objects.filter(
        google_calendar_event_id__isnull=False
    ).update(google_calendar_event_id=None)
    print(f"   {cleared}件のGoogle Calendar IDをクリアしました")
    
    # 4. 修正版の同期ロジックで再同期
    print("\n4. 修正版ロジックで再同期を開始...")
    print("   （繰り返しルールを使用しない個別イベント同期）")
    
    # 同期実行
    sync.sync_all_communities(months_ahead=3)
    
    # 5. 結果確認
    print("\n5. 同期結果の確認...")
    
    # データベースの状態
    db_events = Event.objects.filter(date__gte=today, date__lte=today + timedelta(days=90))
    synced_events = db_events.filter(google_calendar_event_id__isnull=False).exclude(google_calendar_event_id='')
    
    print(f"\nデータベース:")
    print(f"  総イベント数: {db_events.count()}")
    print(f"  同期済み: {synced_events.count()}")
    
    # Googleカレンダーの状態
    new_events = sync.service.list_events(
        time_min=datetime.combine(today, datetime.min.time()),
        time_max=datetime.combine(today + timedelta(days=90), datetime.max.time())
    )
    
    print(f"\nGoogleカレンダー:")
    print(f"  イベント数: {len(new_events)}")
    
    # 重複チェック
    event_counts = {}
    for event in new_events:
        key = f"{event.get('summary', '')}_{event.get('start', {}).get('dateTime', '')[:10]}"
        event_counts[key] = event_counts.get(key, 0) + 1
    
    duplicates = [(k, v) for k, v in event_counts.items() if v > 1]
    
    if duplicates:
        print(f"\n⚠️ 重複が検出されました:")
        for key, count in duplicates[:10]:
            print(f"  {key}: {count}件")
    else:
        print("\n✓ 重複はありません")
    
    print("\n=== 完了 ===")

if __name__ == '__main__':
    clean_and_resync()