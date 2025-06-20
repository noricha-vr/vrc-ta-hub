#!/usr/bin/env python
"""最終的なクリーンアップと同期"""

import os
import sys
import django
from datetime import datetime, timedelta
import time

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from django.utils import timezone
from event.models import Event
from event.sync_to_google import DatabaseToGoogleSync

def final_cleanup_and_sync():
    """最終的なクリーンアップと同期"""
    print("=== 最終的なクリーンアップと同期 ===\n")
    
    sync = DatabaseToGoogleSync()
    today = timezone.now().date()
    
    # 1. Googleカレンダーを完全にクリア
    print("1. Googleカレンダーを完全にクリア...")
    
    # 過去から未来まで広範囲で取得
    all_events = sync.service.list_events(
        time_min=datetime(2024, 1, 1),
        time_max=datetime(2026, 12, 31)
    )
    
    print(f"   削除対象イベント数: {len(all_events)}")
    
    for event in all_events:
        try:
            sync.service.delete_event(event['id'])
        except:
            pass
    
    print("   削除完了")
    
    # 2. データベースのGoogle Calendar IDを完全にクリア
    print("\n2. データベースのGoogle Calendar IDをクリア...")
    Event.objects.update(google_calendar_event_id=None)
    print("   完了")
    
    # 3. 少し待機（Google側の処理を確実に完了させる）
    print("\n3. Google側の処理完了を待機中...")
    time.sleep(5)
    
    # 4. 今日から3ヶ月分のイベントのみ同期
    print("\n4. 今日から3ヶ月分のイベントを同期...")
    
    end_date = today + timedelta(days=90)
    
    # コミュニティごとに順次処理
    from community.models import Community
    communities = Community.objects.filter(status='approved').order_by('name')
    
    total_created = 0
    
    for i, community in enumerate(communities):
        # 進捗表示
        if i % 10 == 0:
            print(f"\n   処理中: {i}/{communities.count()}")
        
        # このコミュニティのイベントを取得
        events = Event.objects.filter(
            community=community,
            date__gte=today,
            date__lte=end_date
        ).order_by('date')
        
        for event in events:
            try:
                # 個別にイベントを作成
                sync._create_google_event(event)
                total_created += 1
            except Exception as e:
                print(f"   エラー: {community.name} - {event.date}: {e}")
    
    print(f"\n   同期完了: {total_created}件のイベントを作成")
    
    # 5. 最終確認
    print("\n5. 最終確認...")
    
    # Googleカレンダーの状態
    final_events = sync.service.list_events(
        time_min=datetime.combine(today, datetime.min.time()),
        time_max=datetime.combine(end_date, datetime.max.time())
    )
    
    print(f"\nGoogleカレンダー: {len(final_events)}件")
    
    # 重複チェック
    event_dict = {}
    duplicates = []
    
    for event in final_events:
        key = f"{event.get('summary', '')}_{event.get('start', {}).get('dateTime', '')[:16]}"
        if key in event_dict:
            duplicates.append(key)
        else:
            event_dict[key] = event
    
    if duplicates:
        print(f"\n⚠️ 重複: {len(duplicates)}件")
        for dup in duplicates[:5]:
            print(f"  {dup}")
    else:
        print("\n✅ 重複なし")
    
    # データベースの同期状況
    db_events = Event.objects.filter(date__gte=today, date__lte=end_date)
    synced = db_events.filter(google_calendar_event_id__isnull=False).exclude(google_calendar_event_id='').count()
    
    print(f"\nデータベース:")
    print(f"  総イベント数: {db_events.count()}")
    print(f"  同期済み: {synced}")
    
    print("\n=== 完了 ===")

if __name__ == '__main__':
    final_cleanup_and_sync()