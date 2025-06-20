#!/usr/bin/env python
"""start_dateを使用したイベント生成のテスト"""

import os
import sys
import django
from datetime import datetime, timedelta

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from django.utils import timezone
from event.models import Event, RecurrenceRule
from event.recurrence_service import RecurrenceService
from community.models import Community

def test_event_generation():
    """イベント生成のテスト"""
    
    print("=== start_dateを使用したイベント生成テスト ===\n")
    
    # テスト対象のコミュニティ
    test_communities = [
        (75, "AI集会ゆる雑談Week"),      # 月曜隔週A (6/16開催)
        (76, "AI集会テックWeek"),        # 月曜隔週B (6/9開催)
        (11, "CS集会"),                  # 火曜隔週A (6/17開催)
        (14, "電子工作"),                # 水曜毎週
        (26, "株式投資座談会"),          # 土曜毎週
    ]
    
    service = RecurrenceService()
    today = timezone.now().date()
    
    for community_id, name in test_communities:
        try:
            community = Community.objects.get(id=community_id)
            master_event = community.events.filter(is_recurring_master=True).first()
            
            if not master_event or not master_event.recurrence_rule:
                print(f"⚠️ {name}: マスターイベントまたはルールが見つかりません")
                continue
            
            rule = master_event.recurrence_rule
            
            print(f"\n{name} (ID: {community_id}):")
            print(f"  開催曜日: {community.weekdays}")
            print(f"  頻度: {rule.frequency} (間隔: {rule.interval})")
            print(f"  start_date: {rule.start_date} ({rule.start_date.strftime('%A') if rule.start_date else 'なし'})")
            
            # 今後3ヶ月のイベント日付を生成
            dates = service.generate_dates(
                rule=rule,
                base_date=today,
                base_time=master_event.start_time,
                months=3,
                community=community
            )
            
            # 最初の5件を表示
            print(f"  生成された日付 (最初の5件):")
            for i, date in enumerate(dates[:5]):
                print(f"    {date.strftime('%Y-%m-%d (%a)')}")
            
            if len(dates) > 5:
                print(f"    ... 他 {len(dates) - 5}件")
            
            # 期待される次回開催日と比較
            expected_next = None
            if community_id == 75:  # AI集会ゆる雑談Week (月曜隔週、6/16開催)
                expected_next = datetime(2025, 6, 30).date()
            elif community_id == 76:  # AI集会テックWeek (月曜隔週、6/9開催)
                expected_next = datetime(2025, 6, 23).date()
            elif community_id == 11:  # CS集会 (火曜隔週、6/17開催)
                expected_next = datetime(2025, 7, 1).date()
            
            if expected_next and dates:
                actual_next = dates[0]
                match = actual_next == expected_next
                print(f"  次回開催日: {actual_next} (期待値: {expected_next}) {'✓' if match else '✗'}")
            
        except Community.DoesNotExist:
            print(f"\n{name} (ID: {community_id}): コミュニティが見つかりません")
        except Exception as e:
            print(f"\n{name} (ID: {community_id}): エラー - {e}")
    
    # 実際にイベントを生成してみる（dry-run）
    print("\n\n=== 実際のイベント生成シミュレーション (dry-run) ===")
    
    # CS集会で試す
    try:
        community = Community.objects.get(id=11)
        master_event = community.events.filter(is_recurring_master=True).first()
        
        if master_event and master_event.recurrence_rule:
            rule = master_event.recurrence_rule
            
            # 既存のイベントを表示
            existing_events = community.events.filter(
                date__gte=today
            ).order_by('date')[:5]
            
            print(f"\n{community.name} の既存イベント:")
            for event in existing_events:
                print(f"  {event.date.strftime('%Y-%m-%d (%a)')}")
            
            # 新規生成予定のイベント
            dates = service.generate_dates(
                rule=rule,
                base_date=today,
                base_time=master_event.start_time,
                months=3,
                community=community
            )
            
            new_dates = []
            for date in dates:
                exists = Event.objects.filter(
                    community=community,
                    date=date,
                    start_time=master_event.start_time
                ).exists()
                if not exists:
                    new_dates.append(date)
            
            print(f"\n新規生成予定のイベント:")
            for date in new_dates[:5]:
                print(f"  {date.strftime('%Y-%m-%d (%a)')}")
            
    except Community.DoesNotExist:
        print("CS集会が見つかりません")

if __name__ == '__main__':
    test_event_generation()