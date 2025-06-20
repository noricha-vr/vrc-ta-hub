#!/usr/bin/env python
"""既存のRecurrenceRuleレコードにstart_dateを設定"""

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

def populate_start_dates():
    """既存のRecurrenceRuleレコードにstart_dateを設定"""
    
    print("=== RecurrenceRuleのstart_date設定 ===\n")
    
    # 定期ルールを持つイベントを取得
    recurring_masters = Event.objects.filter(
        is_recurring_master=True,
        recurrence_rule__isnull=False
    ).select_related('recurrence_rule', 'community')
    
    print(f"処理対象: {recurring_masters.count()}件\n")
    
    updated_count = 0
    biweekly_a_count = 0
    biweekly_b_count = 0
    
    for event in recurring_masters:
        rule = event.recurrence_rule
        community = event.community
        
        # すでにstart_dateが設定されている場合はスキップ
        if rule.start_date:
            print(f"スキップ: {community.name} - すでにstart_dateが設定済み")
            continue
        
        # 開催日付を基準にstart_dateを設定
        if rule.frequency == 'WEEKLY':
            if rule.interval == 1:
                # 毎週の場合は最初のイベントの日付
                first_event = community.events.order_by('date').first()
                if first_event:
                    rule.start_date = first_event.date
                    rule.save()
                    print(f"✓ {community.name}: 毎週 - start_date={rule.start_date}")
                    updated_count += 1
            
            elif rule.interval == 2:
                # 隔週の場合はcustom_ruleのA/B情報を使用
                if rule.custom_rule:
                    # 直近のイベント日付を取得
                    recent_event = community.events.filter(
                        date__lte=timezone.now().date()
                    ).order_by('-date').first()
                    
                    if recent_event:
                        if 'biweekly_A' in rule.custom_rule:
                            # グループA（奇数週）の場合、奇数週の日曜日を起点にする
                            # 2025年1月5日（日）は第1週の日曜日
                            base_date = datetime(2025, 1, 5).date()
                            rule.start_date = base_date
                            rule.save()
                            print(f"✓ {community.name}: 隔週A - start_date={rule.start_date} (基準: {recent_event.date})")
                            biweekly_a_count += 1
                            updated_count += 1
                        
                        elif 'biweekly_B' in rule.custom_rule:
                            # グループB（偶数週）の場合、偶数週の日曜日を起点にする
                            # 2025年1月12日（日）は第2週の日曜日
                            base_date = datetime(2025, 1, 12).date()
                            rule.start_date = base_date
                            rule.save()
                            print(f"✓ {community.name}: 隔週B - start_date={rule.start_date} (基準: {recent_event.date})")
                            biweekly_b_count += 1
                            updated_count += 1
                        else:
                            # A/Bの指定がない場合は直近のイベント日付を使用
                            rule.start_date = recent_event.date
                            rule.save()
                            print(f"✓ {community.name}: 隔週（グループ不明） - start_date={rule.start_date}")
                            updated_count += 1
                    else:
                        print(f"⚠️ {community.name}: イベント履歴なし")
        
        elif rule.frequency == 'MONTHLY_BY_WEEK':
            # 月ごとの場合も最初のイベントの日付
            first_event = community.events.order_by('date').first()
            if first_event:
                rule.start_date = first_event.date
                rule.save()
                print(f"✓ {community.name}: 月1回 - start_date={rule.start_date}")
                updated_count += 1
    
    print(f"\n=== 更新完了 ===")
    print(f"合計: {updated_count}件")
    print(f"隔週A: {biweekly_a_count}件")
    print(f"隔週B: {biweekly_b_count}件")
    
    # 検証: start_dateが正しく機能するか確認
    print("\n=== 動作検証 ===")
    
    # 隔週Aの例
    test_communities = [
        (75, "AI集会ゆる雑談Week", "隔週A"),
        (76, "AI集会テックWeek", "隔週B"),
        (11, "CS集会", "隔週A"),
    ]
    
    for community_id, name, expected_group in test_communities:
        try:
            community = Community.objects.get(id=community_id)
            event = community.events.filter(is_recurring_master=True).first()
            
            if event and event.recurrence_rule:
                rule = event.recurrence_rule
                
                print(f"\n{name} (ID: {community_id}):")
                print(f"  start_date: {rule.start_date}")
                print(f"  custom_rule: {rule.custom_rule}")
                
                # 次の3回の開催日を計算
                test_dates = []
                current_date = timezone.now().date()
                
                for _ in range(5):
                    next_date = rule.get_next_occurrence(current_date)
                    if next_date:
                        test_dates.append(next_date)
                        current_date = next_date + timedelta(days=1)
                
                print(f"  次回開催予定: {', '.join(d.strftime('%m/%d') for d in test_dates[:3])}")
                
        except Community.DoesNotExist:
            print(f"\n{name} (ID: {community_id}): 見つかりません")

if __name__ == '__main__':
    populate_start_dates()