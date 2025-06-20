#!/usr/bin/env python
"""大量重複の調査"""

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
from event.models import Event
from event.sync_to_google import DatabaseToGoogleSync

def investigate_massive_duplicates():
    """大量重複の詳細調査"""
    print("=== Googleカレンダーの大量重複調査 ===\n")
    
    sync = DatabaseToGoogleSync()
    today = timezone.now().date()
    end_date = today + timedelta(days=365)  # 1年先まで
    
    # すべてのイベントを取得
    print("Googleカレンダーから全イベントを取得中...")
    all_events = sync.service.list_events(
        time_min=datetime.combine(today, datetime.min.time()),
        time_max=datetime.combine(end_date, datetime.max.time())
    )
    
    print(f"\n取得したイベント総数: {len(all_events)}件")
    
    # イベントをグループ化
    event_groups = defaultdict(list)
    event_by_date = defaultdict(list)
    
    for event in all_events:
        summary = event.get('summary', 'No title')
        start = event.get('start', {})
        
        # 日時の取得
        if 'dateTime' in start:
            start_datetime = start['dateTime']
            date_str = start_datetime.split('T')[0]
        elif 'date' in start:
            date_str = start['date']
        else:
            date_str = 'Unknown'
        
        # グループ化
        key = f"{summary}_{date_str}"
        event_groups[key].append(event)
        event_by_date[date_str].append(event)
    
    # 重複の統計
    print("\n## 重複の統計")
    duplicate_counts = defaultdict(int)
    total_duplicates = 0
    
    for key, events in event_groups.items():
        count = len(events)
        if count > 1:
            duplicate_counts[count] += 1
            total_duplicates += count - 1
            
            # 重複が多いものを表示
            if count > 5:
                summary = key.split('_')[0]
                date = key.split('_')[1]
                print(f"  - {summary} ({date}): {count}件の重複")
    
    print(f"\n重複グループ数: {sum(duplicate_counts.values())}グループ")
    print(f"余分なイベント数: {total_duplicates}件")
    
    # 重複数の分布
    print("\n## 重複数の分布")
    for count, groups in sorted(duplicate_counts.items()):
        print(f"  {count}重複: {groups}グループ")
    
    # 日付ごとのイベント数
    print("\n## 日付ごとのイベント数（上位10日）")
    date_counts = [(date, len(events)) for date, events in event_by_date.items()]
    date_counts.sort(key=lambda x: x[1], reverse=True)
    
    for date, count in date_counts[:10]:
        print(f"  {date}: {count}件")
        # その日のイベントの内訳
        summary_counts = defaultdict(int)
        for event in event_by_date[date]:
            summary = event.get('summary', 'No title')
            summary_counts[summary] += 1
        
        for summary, cnt in sorted(summary_counts.items()):
            if cnt > 1:
                print(f"    - {summary}: {cnt}件")
    
    # データベースとの比較
    print("\n## データベースとの比較")
    db_events = Event.objects.filter(date__gte=today).count()
    print(f"データベースのイベント数: {db_events}件")
    print(f"Googleカレンダーのイベント数: {len(all_events)}件")
    print(f"差分: {len(all_events) - db_events}件")
    
    # 同期の問題を特定
    print("\n## 同期の問題分析")
    
    # 同じGoogle Calendar IDを持つイベントがあるか
    google_ids = defaultdict(list)
    for event in all_events:
        event_id = event.get('id')
        google_ids[event_id].append(event)
    
    duplicate_ids = [(id, events) for id, events in google_ids.items() if len(events) > 1]
    if duplicate_ids:
        print(f"\n同じGoogle Calendar IDを持つイベント: {len(duplicate_ids)}件")
    else:
        print("\n同じGoogle Calendar IDを持つイベントはありません")
    
    # Recurring eventの確認
    print("\n## Recurring Event（繰り返しイベント）の確認")
    recurring_count = 0
    for event in all_events:
        if 'recurringEventId' in event or 'recurrence' in event:
            recurring_count += 1
    
    print(f"Recurring Event数: {recurring_count}件")
    
    return len(all_events), total_duplicates

if __name__ == '__main__':
    total, duplicates = investigate_massive_duplicates()
    
    if duplicates > 0:
        print(f"\n⚠️ 大量の重複が検出されました！")
        print(f"削除が必要なイベント数: {duplicates}件")