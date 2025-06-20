#!/usr/bin/env python
"""
1ヶ月以上先のイベントをデータベースとGoogleカレンダーから削除するスクリプト
"""
import os
import sys
import django
from datetime import datetime, timedelta
from django.utils import timezone

# Djangoの設定
sys.path.append('/opt/project/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from event.models import Event
from event.google_calendar import GoogleCalendarService
from website.settings import GOOGLE_CALENDAR_ID, GOOGLE_CALENDAR_CREDENTIALS, DEBUG


def delete_future_events():
    """1ヶ月以上先のイベントを削除"""
    # 現在の日時と1ヶ月後の日時を計算
    now = timezone.now()
    one_month_later = now + timedelta(days=30)
    
    print(f"現在の日時: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"1ヶ月後の日時: {one_month_later.strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 50)
    
    # 1ヶ月以上先のイベントを取得
    future_events = Event.objects.filter(date__gt=one_month_later.date()).order_by('date', 'start_time')
    
    print(f"\n削除対象のイベント数: {future_events.count()}件")
    
    if future_events.count() == 0:
        print("削除対象のイベントがありません。")
        return
    
    # 削除対象のイベントを表示
    print("\n削除対象のイベント一覧:")
    print("-" * 80)
    for event in future_events:
        print(f"ID: {event.id} | {event.community.name} | {event.date} {event.start_time} | GoogleID: {event.google_calendar_event_id or 'なし'}")
    
    # 確認プロンプト
    print("\n" + "=" * 80)
    confirm = input(f"上記の {future_events.count()} 件のイベントを削除してもよろしいですか？ (yes/no): ")
    
    if confirm.lower() != 'yes':
        print("削除を中止しました。")
        return
    
    # Google Calendar APIの初期化
    try:
        calendar_service = GoogleCalendarService(
            calendar_id=GOOGLE_CALENDAR_ID,
            credentials_path=GOOGLE_CALENDAR_CREDENTIALS if DEBUG else None
        )
        google_calendar_available = True
    except Exception as e:
        print(f"\nGoogleカレンダーAPIの初期化に失敗しました: {e}")
        google_calendar_available = False
    
    # 削除処理
    deleted_count = 0
    google_deleted_count = 0
    google_failed_count = 0
    
    for event in future_events:
        # Googleカレンダーから削除
        if google_calendar_available and event.google_calendar_event_id:
            try:
                calendar_service.delete_event(event.google_calendar_event_id)
                google_deleted_count += 1
                print(f"✓ Googleカレンダーから削除: {event.community.name} - {event.date}")
            except Exception as e:
                google_failed_count += 1
                print(f"✗ Googleカレンダー削除失敗: {event.community.name} - {event.date} - エラー: {e}")
        
        # データベースから削除
        event.delete()
        deleted_count += 1
        print(f"✓ データベースから削除: {event.community.name} - {event.date}")
    
    # 結果のサマリー
    print("\n" + "=" * 80)
    print("削除処理が完了しました。")
    print(f"データベースから削除: {deleted_count}件")
    if google_calendar_available:
        print(f"Googleカレンダーから削除: {google_deleted_count}件")
        if google_failed_count > 0:
            print(f"Googleカレンダー削除失敗: {google_failed_count}件")
    else:
        print("Googleカレンダーへの接続ができなかったため、Googleカレンダーからの削除はスキップされました。")
    
    # 削除後の確認
    remaining_future_events = Event.objects.filter(date__gt=one_month_later.date()).count()
    print(f"\n削除後の1ヶ月以上先のイベント数: {remaining_future_events}件")


if __name__ == '__main__':
    delete_future_events()