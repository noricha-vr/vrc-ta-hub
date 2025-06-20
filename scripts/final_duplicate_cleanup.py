#!/usr/bin/env python
"""最終的な重複削除"""

import os
import sys
import django

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from event.google_calendar import GoogleCalendarService
from website.settings import GOOGLE_CALENDAR_ID, GOOGLE_CALENDAR_CREDENTIALS
from event.models import Event

def final_duplicate_cleanup():
    """最終的な重複削除"""
    print("=== 最終的な重複削除 ===\n")
    
    service = GoogleCalendarService(
        calendar_id=GOOGLE_CALENDAR_ID,
        credentials_path=GOOGLE_CALENDAR_CREDENTIALS
    )
    
    # 削除対象のID（データベースに登録されていないもの）
    duplicate_ids = [
        # VRC Blender集会(Mimi)の重複
        'dp5ukhe1j6sf5at4nsbgthhdq8',
        'opo17as0331g75hd3jrtcgc504',
        # ITインフラ集会の重複
        '3v4lv6jc1gmalct1jj7l9l6tfg',
        '5fp32n1k53jgvdetgjjmohojv0',
        # AI集会ゆる雑談Weekの重複
        '72mqtg6nk55c7isook7lv9nud4',
        '7c2msjrn9df633lgvk7vja4lro',
        'e1lnm6ldmo6tvb4m46d90a4nsk'
    ]
    
    # データベースに登録されているIDを確認
    db_ids = set(Event.objects.filter(
        google_calendar_event_id__in=duplicate_ids
    ).values_list('google_calendar_event_id', flat=True))
    
    print(f"データベースに登録されているID: {len(db_ids)}件")
    
    # 各IDを確認して、DBに登録されていないものだけ削除
    deleted = 0
    kept = 0
    
    for event_id in duplicate_ids:
        if event_id in db_ids:
            print(f"✓ 保持: {event_id} (DBに登録あり)")
            kept += 1
        else:
            try:
                service.service.events().delete(
                    calendarId=service.calendar_id,
                    eventId=event_id
                ).execute()
                deleted += 1
                print(f"✗ 削除: {event_id} (DBに登録なし)")
            except Exception as e:
                print(f"✗ 削除失敗: {event_id} - {str(e)[:50]}")
    
    print(f"\n結果: {kept}件保持、{deleted}件削除")
    
    # 最終確認
    from datetime import datetime, timedelta
    from django.utils import timezone
    
    today = timezone.now().date()
    all_events = service.list_events(
        time_min=datetime.combine(today, datetime.min.time()),
        time_max=datetime.combine(today + timedelta(days=7), datetime.max.time())
    )
    
    # 重複チェック
    event_dict = {}
    duplicates = []
    
    for event in all_events:
        key = f"{event.get('summary', '')}_{event.get('start', {}).get('dateTime', '')[:16]}"
        if key in event_dict:
            duplicates.append(key)
        else:
            event_dict[key] = event
    
    print(f"\n最終確認:")
    print(f"Googleカレンダーのイベント数: {len(all_events)}")
    print(f"重複: {len(duplicates)}件")
    
    if len(duplicates) == 0:
        print("\n✅ 重複が解消されました！")
    else:
        print("\n⚠️ まだ重複があります:")
        for dup in duplicates:
            print(f"  {dup}")

if __name__ == '__main__':
    final_duplicate_cleanup()