#!/usr/bin/env python
"""Googleカレンダーの今日以降のイベントを全削除"""

import os
import sys
import django
from datetime import datetime, timedelta

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from django.conf import settings
from event.google_calendar import GoogleCalendarService


def clear_future_events(dry_run=True):
    """今日以降のGoogleカレンダーイベントを削除"""
    calendar_id = settings.GOOGLE_CALENDAR_ID
    credentials_path = settings.GOOGLE_CALENDAR_CREDENTIALS
    service = GoogleCalendarService(calendar_id, credentials_path=credentials_path)
    
    print('=== Googleカレンダーのクリア ===')
    print(f'DRY RUN: {dry_run}')
    print('='*60)
    
    # 今日から2ヶ月先まで（十分な範囲）
    today = datetime.now()
    end_date = today + timedelta(days=60)
    
    print(f'削除対象期間: {today.date()} から {end_date.date()} まで')
    
    # イベントを取得
    print('\nイベントを取得中...')
    events = service.list_events(
        time_min=today,
        time_max=end_date,
        max_results=1000
    )
    
    print(f'見つかったイベント: {len(events)}件')
    
    if not events:
        print('削除対象のイベントはありません。')
        return
    
    # コミュニティ別に集計
    community_counts = {}
    for event in events:
        summary = event.get('summary', '不明')
        if summary not in community_counts:
            community_counts[summary] = 0
        community_counts[summary] += 1
    
    print('\nコミュニティ別のイベント数:')
    for community, count in sorted(community_counts.items()):
        print(f'  {community}: {count}件')
    
    # 削除実行
    deleted_count = 0
    error_count = 0
    
    if not dry_run:
        print('\n削除を実行中...')
        for i, event in enumerate(events):
            try:
                service.delete_event(event['id'])
                deleted_count += 1
                if (i + 1) % 10 == 0:
                    print(f'  {i + 1}/{len(events)} 件削除完了')
            except Exception as e:
                error_count += 1
                print(f'  エラー: {event.get("summary", "不明")} - {e}')
    
    print(f'\n=== 結果 ===')
    if dry_run:
        print(f'削除予定: {len(events)}件')
        print('\n※ DRY RUNモードです。実際の削除は行われていません。')
        print('実際に削除するには --apply オプションを使用してください。')
    else:
        print(f'削除成功: {deleted_count}件')
        print(f'エラー: {error_count}件')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Googleカレンダーの今日以降のイベントを削除')
    parser.add_argument('--apply', action='store_true', help='実際に削除を実行')
    args = parser.parse_args()
    
    clear_future_events(dry_run=not args.apply)