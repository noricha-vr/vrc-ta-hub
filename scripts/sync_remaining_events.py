#!/usr/bin/env python
"""未同期のイベントをGoogleカレンダーに同期"""

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


def sync_remaining_events():
    """未同期のイベントを同期"""
    
    print("=== 未同期イベントの同期 ===\n")
    
    # 未同期のイベントを取得
    today = timezone.now().date()
    unsynced_events = Event.objects.filter(
        date__gte=today,
        google_calendar_event_id__isnull=True
    ).select_related('community').order_by('community__id', 'date')
    
    total_unsynced = unsynced_events.count()
    print(f"未同期イベント数: {total_unsynced}件\n")
    
    if total_unsynced == 0:
        print("すべてのイベントが同期済みです")
        return
    
    # DatabaseToGoogleSyncを初期化
    sync_service = DatabaseToGoogleSync()
    
    # コミュニティごとに同期
    current_community = None
    community_count = 0
    event_count = 0
    error_count = 0
    
    for event in unsynced_events:
        if current_community != event.community:
            if current_community:
                print(f"  完了: {community_count}件同期\n")
            current_community = event.community
            community_count = 0
            print(f"{event.community.name}:")
        
        try:
            # イベント情報を作成
            start_datetime = timezone.make_aware(
                datetime.combine(event.date, event.start_time),
                timezone.get_current_timezone()
            )
            end_datetime = start_datetime + timedelta(minutes=event.duration)
            
            # 説明文を作成
            description = f"集会: {event.community.name}"
            if event.community.description:
                description += f"\n\n{event.community.description}"
            
            # Googleカレンダーに作成
            result = sync_service.service.service.events().insert(
                calendarId=sync_service.calendar_id,
                body={
                    'summary': event.community.name,
                    'start': {
                        'dateTime': start_datetime.isoformat(),
                        'timeZone': 'Asia/Tokyo',
                    },
                    'end': {
                        'dateTime': end_datetime.isoformat(),
                        'timeZone': 'Asia/Tokyo',
                    },
                    'description': description,
                }
            ).execute()
            
            # IDを保存
            event.google_calendar_event_id = result['id']
            event.save()
            
            event_count += 1
            community_count += 1
            
            # 進捗表示
            if event_count % 10 == 0:
                print(f"  進捗: {event_count}/{total_unsynced}件 ({event_count/total_unsynced*100:.1f}%)")
            
            # APIレート制限対策
            time.sleep(0.1)
            
        except Exception as e:
            print(f"  ✗ エラー: {event.date} - {e}")
            error_count += 1
            
            # エラーが多い場合は中断
            if error_count > 10:
                print("\nエラーが多いため同期を中断します")
                break
    
    if current_community:
        print(f"  完了: {community_count}件同期")
    
    print(f"\n=== 同期完了 ===")
    print(f"同期成功: {event_count}件")
    print(f"エラー: {error_count}件")
    print(f"残り: {total_unsynced - event_count}件")


if __name__ == '__main__':
    sync_remaining_events()