#!/usr/bin/env python
"""1ヶ月以上先のイベントを削除"""

import os
import sys
import django
from datetime import datetime, timedelta

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from django.utils import timezone
from event.google_calendar import GoogleCalendarService
from event.models import Event
from django.conf import settings


def delete_far_future_events():
    """1ヶ月以上先のイベントを削除"""
    
    print("=== 1ヶ月以上先のイベント削除 ===\n")
    
    # 基準日を設定（今日から30日後）
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff_date = today + timedelta(days=30)
    
    print(f"基準日: {cutoff_date.strftime('%Y-%m-%d')}")
    print("これより後のイベントを削除します\n")
    
    # 1. データベースから削除
    print("1. データベースのイベントを確認...")
    db_events = Event.objects.filter(date__gt=cutoff_date.date())
    db_count = db_events.count()
    
    if db_count > 0:
        print(f"   {db_count}件のイベントが見つかりました")
        db_events.delete()
        print(f"   削除完了\n")
    else:
        print("   該当するイベントはありません\n")
    
    # 2. Googleカレンダーから削除
    print("2. Googleカレンダーのイベントを確認...")
    
    # GoogleCalendarServiceを初期化
    service = GoogleCalendarService(
        calendar_id=settings.GOOGLE_CALENDAR_ID,
        credentials_path=settings.GOOGLE_CALENDAR_CREDENTIALS
    )
    
    # Googleカレンダーからイベントを取得（90日分）
    time_min = cutoff_date.isoformat() + 'Z'
    time_max = (today + timedelta(days=90)).isoformat() + 'Z'
    
    try:
        events_result = service.service.events().list(
            calendarId=service.calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            maxResults=2500,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        google_events = events_result.get('items', [])
        print(f"   {len(google_events)}件のイベントが見つかりました")
        
        if google_events:
            print("\n   削除対象の最初の5件:")
            for i, event in enumerate(google_events[:5]):
                start = event['start'].get('dateTime', event['start'].get('date'))
                summary = event.get('summary', 'No Title')
                print(f"   - {start[:10]} {summary}")
            
            if len(google_events) > 5:
                print(f"   ... 他 {len(google_events) - 5}件")
            
            print(f"\n   {len(google_events)}件のイベントを削除しますか？")
            print("   続行する場合は 'yes' と入力してください: ", end='')
            
            # Docker環境では自動的に yes とする
            response = 'yes'
            print(response)
            
            if response.lower() == 'yes':
                deleted_count = 0
                error_count = 0
                
                for event in google_events:
                    try:
                        service.service.events().delete(
                            calendarId=service.calendar_id,
                            eventId=event['id']
                        ).execute()
                        deleted_count += 1
                        
                        # 進捗表示
                        if deleted_count % 10 == 0:
                            print(f"   削除中... {deleted_count}/{len(google_events)}")
                        
                    except Exception as e:
                        error_count += 1
                        if error_count <= 5:  # 最初の5件のエラーのみ表示
                            print(f"   エラー: {event.get('summary', 'Unknown')} - {e}")
                
                print(f"\n   削除完了")
                print(f"   成功: {deleted_count}件")
                print(f"   エラー: {error_count}件")
            else:
                print("   削除をキャンセルしました")
        else:
            print("   該当するイベントはありません")
        
    except Exception as e:
        print(f"エラー: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n=== 完了 ===")
    print(f"データベース: {db_count}件削除")
    print(f"Googleカレンダー: {deleted_count if 'deleted_count' in locals() else 0}件削除")


if __name__ == '__main__':
    delete_far_future_events()