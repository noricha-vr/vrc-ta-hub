#!/usr/bin/env python
"""重複チェックのみを実行"""

import os
import sys
import django
from datetime import datetime, timedelta

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from django.utils import timezone
from django.db.models import Count
from event.models import Event
from community.models import Community
from event.sync_to_google import DatabaseToGoogleSync

def check_duplicates():
    """重複チェック"""
    print("=== 重複チェックを実行します ===\n")
    
    # データベース内の重複チェック
    print("1. データベース内の重複チェック")
    
    # 今日以降のイベント総数
    today = timezone.now().date()
    total_events = Event.objects.filter(date__gte=today).count()
    print(f"今日以降のイベント総数: {total_events}件")
    
    # 重複チェック
    duplicates = Event.objects.filter(
        date__gte=today
    ).values('community', 'date', 'start_time').annotate(
        count=Count('id')
    ).filter(count__gt=1)
    
    if duplicates:
        print(f"\n⚠️ データベースに{len(duplicates)}件の重複が見つかりました:")
        for dup in duplicates:
            community = Community.objects.get(id=dup['community'])
            print(f"  - {community.name}: {dup['date']} {dup['start_time']} ({dup['count']}件)")
    else:
        print("\n✓ データベースに重複はありません")
    
    # Googleカレンダーの重複チェック
    print("\n2. Googleカレンダー内の重複チェック")
    
    sync = DatabaseToGoogleSync()
    end_date = today + timedelta(days=90)
    
    all_events = sync.service.list_events(
        time_min=datetime.combine(today, datetime.min.time()),
        time_max=datetime.combine(end_date, datetime.max.time())
    )
    
    print(f"Googleカレンダーのイベント総数: {len(all_events)}件")
    
    # イベントをグループ化
    google_events = {}
    for event in all_events:
        # 開始時刻を正規化（タイムゾーン情報を除去）
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
        print(f"\n⚠️ Googleカレンダーに{len(google_duplicates)}件の重複が見つかりました:")
        for (summary, start_time), events in google_duplicates:
            print(f"  - {summary}: {start_time} ({len(events)}件)")
            for event in events:
                print(f"    ID: {event['id']}")
    else:
        print("\n✓ Googleカレンダーに重複はありません")
    
    # データベースとGoogleカレンダーの同期状況を確認
    print("\n3. 同期状況の確認")
    
    # google_calendar_event_idが設定されているイベント数
    synced_events = Event.objects.filter(
        date__gte=today,
        google_calendar_event_id__isnull=False
    ).exclude(google_calendar_event_id='').count()
    
    print(f"Google Calendar IDが設定されているイベント: {synced_events}/{total_events}件")
    
    # コミュニティごとのイベント数を表示
    print("\n4. コミュニティごとのイベント数（上位10件）")
    community_counts = Event.objects.filter(
        date__gte=today
    ).values('community__name').annotate(
        count=Count('id')
    ).order_by('-count')[:10]
    
    for cc in community_counts:
        print(f"  - {cc['community__name']}: {cc['count']}件")
    
    return len(duplicates), len(google_duplicates)

if __name__ == '__main__':
    db_dup, google_dup = check_duplicates()
    
    print("\n=== チェック完了 ===")
    if db_dup == 0 and google_dup == 0:
        print("✅ 重複は見つかりませんでした")
    else:
        print(f"⚠️ 重複が検出されました: DB {db_dup}件, Google {google_dup}件")