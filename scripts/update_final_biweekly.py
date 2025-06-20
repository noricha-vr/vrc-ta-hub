#!/usr/bin/env python
"""最終的な隔週パターンの更新"""

import os
import sys
import django
from datetime import datetime, date

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from event.models import RecurrenceRule, Event
from community.models import Community

def get_week_number(target_date):
    """日付から週番号を取得（1月1日を第1週として）"""
    year_start = date(target_date.year, 1, 1)
    days_diff = (target_date - year_start).days
    return (days_diff // 7) + 1

def update_final_patterns():
    """最終的な隔週パターンを更新"""
    
    print("=== 最終的な隔週パターンの更新（本番DB） ===\n")
    
    # 1. セキュリティ集会 in VRChat - 今週土曜日（2025年6月21日）基準
    try:
        print("1. セキュリティ集会 in VRChat")
        community = Community.objects.get(name='セキュリティ集会 in VRChat')
        # 2025年6月21日の週番号を確認
        base_date = date(2025, 6, 21)
        week_num = get_week_number(base_date)
        pattern = 'biweekly_A' if week_num % 2 == 1 else 'biweekly_B'
        
        event = community.events.filter(is_recurring_master=True).first()
        if not event:
            event = community.events.order_by('-date').first()
            
        if event:
            if not event.recurrence_rule:
                rule = RecurrenceRule.objects.create(
                    frequency='WEEKLY',
                    interval=2,
                    custom_rule=pattern
                )
                event.recurrence_rule = rule
                event.is_recurring_master = True
                event.save()
            else:
                rule = event.recurrence_rule
                rule.frequency = 'WEEKLY'
                rule.interval = 2
                rule.custom_rule = pattern
                rule.save()
            print(f"✓ 更新完了 - {pattern}（2025/6/21は第{week_num}週）")
        else:
            print("✗ イベントが見つかりません")
    except Community.DoesNotExist:
        print("✗ コミュニティが見つかりません")
    
    # 2. 「計算と自然」集会 - 直近3回をベースに設定
    try:
        print("\n2. 「計算と自然」集会")
        community = Community.objects.get(name='「計算と自然」集会')
        # 直近3回のイベントから判定（CSVデータより）
        # 分析結果では奇数週6回、偶数週5回で不定だが、直近の傾向を見る
        event = community.events.filter(is_recurring_master=True).first()
        if not event:
            event = community.events.order_by('-date').first()
            
        if event:
            if not event.recurrence_rule:
                rule = RecurrenceRule.objects.create(
                    frequency='WEEKLY',
                    interval=2,
                    custom_rule=''  # 不規則なので空に設定
                )
                event.recurrence_rule = rule
                event.is_recurring_master = True
                event.save()
            else:
                rule = event.recurrence_rule
                rule.frequency = 'WEEKLY'
                rule.interval = 2
                rule.custom_rule = ''  # 不規則なので空に設定
                rule.save()
            print("✓ 更新完了 - 隔週（不規則パターン）")
        else:
            print("✗ イベントが見つかりません")
    except Community.DoesNotExist:
        print("✗ コミュニティが見つかりません")
    
    # 3. アバター改変なんもわからん集会 - 6月25日(水)基準
    try:
        print("\n3. アバター改変なんもわからん集会")
        community = Community.objects.get(name='アバター改変なんもわからん集会')
        # 2025年6月25日の週番号を確認
        base_date = date(2025, 6, 25)
        week_num = get_week_number(base_date)
        pattern = 'biweekly_A' if week_num % 2 == 1 else 'biweekly_B'
        
        event = community.events.filter(is_recurring_master=True).first()
        if not event:
            event = community.events.order_by('-date').first()
            
        if event:
            if not event.recurrence_rule:
                rule = RecurrenceRule.objects.create(
                    frequency='WEEKLY',
                    interval=2,
                    custom_rule=pattern
                )
                event.recurrence_rule = rule
                event.is_recurring_master = True
                event.save()
            else:
                rule = event.recurrence_rule
                rule.frequency = 'WEEKLY'
                rule.interval = 2
                rule.custom_rule = pattern
                rule.save()
            print(f"✓ 更新完了 - {pattern}（2025/6/25は第{week_num}週）")
        else:
            print("✗ イベントが見つかりません")
    except Community.DoesNotExist:
        print("✗ コミュニティが見つかりません")
    
    # 4. シェーダー集会 - 6月16日(月)基準
    try:
        print("\n4. シェーダー集会")
        community = Community.objects.get(name='シェーダー集会')
        # 2025年6月16日の週番号を確認
        base_date = date(2025, 6, 16)
        week_num = get_week_number(base_date)
        pattern = 'biweekly_A' if week_num % 2 == 1 else 'biweekly_B'
        
        event = community.events.filter(is_recurring_master=True).first()
        if not event:
            event = community.events.order_by('-date').first()
            
        if event:
            if not event.recurrence_rule:
                rule = RecurrenceRule.objects.create(
                    frequency='WEEKLY',
                    interval=2,
                    custom_rule=pattern
                )
                event.recurrence_rule = rule
                event.is_recurring_master = True
                event.save()
            else:
                rule = event.recurrence_rule
                rule.frequency = 'WEEKLY'
                rule.interval = 2
                rule.custom_rule = pattern
                rule.save()
            print(f"✓ 更新完了 - {pattern}（2025/6/16は第{week_num}週）")
        else:
            print("✗ イベントが見つかりません")
    except Community.DoesNotExist:
        print("✗ コミュニティが見つかりません")
    
    # 5. だいたいで分かる政治経済 - 不定期で登録
    try:
        print("\n5. だいたいで分かる政治経済")
        community = Community.objects.get(name='だいたいで分かる政治経済')
        event = community.events.filter(is_recurring_master=True).first()
        if not event:
            event = community.events.order_by('-date').first()
            
        if event:
            if not event.recurrence_rule:
                rule = RecurrenceRule.objects.create(
                    frequency='OTHER',
                    interval=1,
                    custom_rule='不定期開催'
                )
                event.recurrence_rule = rule
                event.is_recurring_master = True
                event.save()
            else:
                rule = event.recurrence_rule
                rule.frequency = 'OTHER'
                rule.interval = 1
                rule.custom_rule = '不定期開催'
                rule.save()
            print("✓ 更新完了 - 不定期開催")
        else:
            print("✗ イベントが見つかりません")
    except Community.DoesNotExist:
        print("✗ コミュニティが見つかりません")
    
    print("\n更新が完了しました")

if __name__ == '__main__':
    update_final_patterns()