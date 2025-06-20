#!/usr/bin/env python
"""RecurrenceRuleのstart_dateを実際のイベント日付に基づいて修正"""

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
from community.models import Community

def fix_start_dates():
    """RecurrenceRuleのstart_dateを実際のイベント日付に基づいて修正"""
    
    print("=== RecurrenceRuleのstart_date修正 ===\n")
    
    # 定期ルールを持つイベントを取得
    recurring_masters = Event.objects.filter(
        is_recurring_master=True,
        recurrence_rule__isnull=False
    ).select_related('recurrence_rule', 'community')
    
    print(f"処理対象: {recurring_masters.count()}件\n")
    
    fixed_count = 0
    
    for event in recurring_masters:
        rule = event.recurrence_rule
        community = event.community
        
        if rule.frequency == 'WEEKLY' and rule.interval == 2:
            # 隔週の場合、直近のイベントを基準に設定
            recent_event = community.events.filter(
                date__lte=timezone.now().date()
            ).order_by('-date').first()
            
            if recent_event:
                # custom_ruleからA/Bを判定
                if rule.custom_rule and 'biweekly_A' in rule.custom_rule:
                    # グループA（奇数週）の場合
                    # recent_eventの日付から遡って、週番号が奇数の同じ曜日を探す
                    target_date = recent_event.date
                    while True:
                        # 週番号を計算（1月1日を含む週を第1週とする）
                        week_number = target_date.isocalendar()[1]
                        if week_number % 2 == 1:  # 奇数週
                            rule.start_date = target_date
                            rule.save()
                            print(f"✓ {community.name}: 隔週A - start_date={rule.start_date} (直近イベント: {recent_event.date})")
                            fixed_count += 1
                            break
                        target_date -= timedelta(weeks=2)
                
                elif rule.custom_rule and 'biweekly_B' in rule.custom_rule:
                    # グループB（偶数週）の場合
                    target_date = recent_event.date
                    while True:
                        week_number = target_date.isocalendar()[1]
                        if week_number % 2 == 0:  # 偶数週
                            rule.start_date = target_date
                            rule.save()
                            print(f"✓ {community.name}: 隔週B - start_date={rule.start_date} (直近イベント: {recent_event.date})")
                            fixed_count += 1
                            break
                        target_date -= timedelta(weeks=2)
                
                else:
                    # A/Bの指定がない場合は直近のイベント日付を使用
                    rule.start_date = recent_event.date
                    rule.save()
                    print(f"✓ {community.name}: 隔週（グループ不明） - start_date={rule.start_date}")
                    fixed_count += 1
        
        elif rule.frequency == 'WEEKLY' and rule.interval == 1:
            # 毎週の場合は最も古いイベントの日付を使用
            first_event = community.events.order_by('date').first()
            if first_event and rule.start_date != first_event.date:
                rule.start_date = first_event.date
                rule.save()
                print(f"✓ {community.name}: 毎週 - start_date={rule.start_date}")
                fixed_count += 1
    
    print(f"\n=== 修正完了 ===")
    print(f"修正件数: {fixed_count}件")
    
    # 動作検証
    print("\n=== 動作検証 ===")
    
    test_cases = [
        # (community_id, expected_dates)
        (75, ["2025-06-23", "2025-07-07"]),  # AI集会ゆる雑談Week - 月曜隔週A
        (76, ["2025-06-23", "2025-07-07"]),  # AI集会テックWeek - 月曜隔週B  
        (11, ["2025-06-24", "2025-07-08"]),  # CS集会 - 火曜隔週A
    ]
    
    for community_id, expected in test_cases:
        try:
            community = Community.objects.get(id=community_id)
            event = community.events.filter(is_recurring_master=True).first()
            
            if event and event.recurrence_rule:
                rule = event.recurrence_rule
                
                print(f"\n{community.name} (ID: {community_id}):")
                print(f"  start_date: {rule.start_date} ({rule.start_date.strftime('%A')})")
                print(f"  interval: {rule.interval}")
                print(f"  custom_rule: {rule.custom_rule}")
                
                # 次の2回の開催日を計算
                next_dates = []
                current_date = timezone.now().date()
                
                for _ in range(3):
                    next_date = rule.get_next_occurrence(current_date)
                    if next_date:
                        next_dates.append(next_date)
                        current_date = next_date + timedelta(days=1)
                
                print(f"  次回開催予定: {', '.join(d.strftime('%Y-%m-%d (%a)') for d in next_dates[:2])}")
                
                # 期待値と比較
                if len(next_dates) >= 2:
                    actual = [next_dates[0].strftime('%Y-%m-%d'), next_dates[1].strftime('%Y-%m-%d')]
                    match = actual == expected
                    print(f"  期待値との一致: {'✓' if match else '✗'} (期待: {expected}, 実際: {actual})")
                
        except Community.DoesNotExist:
            print(f"\n{community_id}: コミュニティが見つかりません")

if __name__ == '__main__':
    fix_start_dates()