#!/usr/bin/env python
"""Googleカレンダーのすべてのイベントを削除"""

import os
import sys
import django

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from event.google_calendar import GoogleCalendarService
from website.settings import GOOGLE_CALENDAR_ID, GOOGLE_CALENDAR_CREDENTIALS

def delete_all_google_events():
    """Googleカレンダーのすべてのイベントを削除"""
    print("=== Googleカレンダーの全イベント削除 ===\n")
    
    service = GoogleCalendarService(
        calendar_id=GOOGLE_CALENDAR_ID,
        credentials_path=GOOGLE_CALENDAR_CREDENTIALS
    )
    
    # 特定のイベントIDを直接削除
    event_ids = [
        # エンジニア作業飲み集会
        '4jni8l9ulamnbu7rtesk6o6mk0',
        '8acd4kj9blhr704o91lhketlt0',
        'huagcq457uts7dcf6nj419qb6k',
        # 量子力学のたわいのない雑談の会
        '1j5gp3rtts0bjo32v540nb1ka0',
        '89emrd8jqq24vphgale8heub40',
        'aaklq1kbm3jm568mosvp6fcbq4',
        # 株式投資座談会
        '1qs0lupkutqb3ujj73ugfjj2tk',
        'gva1ovoed5lacl5m5llaeq2r84',
        'o4cjt68km5uqdcapdq3805vjgo',
        # その他（重複チェックで見つからなかったもの）
        'me6j5s7mhhpacjqgs5qn871tl4',
        'sefrno37ip3bc1tkhojn1d1ces',
        'ifpilggvkk67kl4pl7spfdfdk0',
        'jvs5r71rmigqejf8fgnmdf29b0'
    ]
    
    deleted = 0
    for event_id in event_ids:
        try:
            service.service.events().delete(
                calendarId=service.calendar_id,
                eventId=event_id
            ).execute()
            deleted += 1
            print(f"✓ 削除: {event_id}")
        except Exception as e:
            print(f"✗ 削除失敗: {event_id} - {str(e)[:50]}")
    
    print(f"\n{deleted}件削除しました")
    
    # 念のため、リスト取得して残りがあれば削除
    print("\n残りのイベントを確認中...")
    
    from datetime import datetime
    all_events = service.list_events(
        time_min=datetime(2024, 1, 1),
        time_max=datetime(2026, 12, 31)
    )
    
    if all_events:
        print(f"\nまだ{len(all_events)}件のイベントが残っています")
        for event in all_events:
            try:
                service.delete_event(event['id'])
                deleted += 1
                print(f"✓ 追加削除: {event.get('summary', 'Unknown')}")
            except:
                pass
    
    print(f"\n合計{deleted}件のイベントを削除しました")
    
    # データベースのGoogle Calendar IDもクリア
    from event.models import Event
    Event.objects.update(google_calendar_event_id=None)
    print("\nデータベースのGoogle Calendar IDもクリアしました")

if __name__ == '__main__':
    delete_all_google_events()