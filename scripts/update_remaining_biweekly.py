#!/usr/bin/env python
"""残りの隔週パターンを更新"""

import os
import sys
import django

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from event.models import RecurrenceRule, Event
from community.models import Community

def update_remaining_biweekly_patterns():
    """残りの隔週パターンを更新"""
    
    # 奇数週開催グループ（biweekly_A）- 追加分
    additional_biweekly_a = [
        'ゲーム開発集会Ⅲ',
        '妖怪好き交流所『怪し火-AYASHIBI-』',
    ]
    
    # 偶数週開催グループ（biweekly_B）- 追加分
    additional_biweekly_b = [
        'ITエンジニア キャリア相談・雑談集会',
        'データサイエンティスト集会',
        '論文紹介集会',
    ]
    
    print("=== 残りの隔週パターンの更新 ===\n")
    
    # 奇数週グループの更新
    print("## 追加の奇数週開催グループ（第1・3週）")
    for name in additional_biweekly_a:
        try:
            community = Community.objects.get(name=name)
            event = community.events.filter(is_recurring_master=True).first()
            if event and event.recurrence_rule:
                rule = event.recurrence_rule
                rule.frequency = 'WEEKLY'
                rule.interval = 2
                rule.custom_rule = 'biweekly_A'
                rule.save()
                print(f"✓ {name}")
            else:
                # RecurrenceRuleがない場合は新規作成を試みる
                event = community.events.order_by('-date').first()
                if event:
                    rule = RecurrenceRule.objects.create(
                        frequency='WEEKLY',
                        interval=2,
                        custom_rule='biweekly_A'
                    )
                    event.recurrence_rule = rule
                    event.is_recurring_master = True
                    event.save()
                    print(f"✓ {name} - 新規RecurrenceRuleを作成")
                else:
                    print(f"✗ {name} - イベントが見つかりません")
        except Community.DoesNotExist:
            print(f"✗ {name} - コミュニティが見つかりません")
    
    # 偶数週グループの更新
    print("\n## 追加の偶数週開催グループ（第2・4週）")
    for name in additional_biweekly_b:
        try:
            community = Community.objects.get(name=name)
            event = community.events.filter(is_recurring_master=True).first()
            if event and event.recurrence_rule:
                rule = event.recurrence_rule
                rule.frequency = 'WEEKLY'
                rule.interval = 2
                rule.custom_rule = 'biweekly_B'
                rule.save()
                print(f"✓ {name}")
            else:
                # RecurrenceRuleがない場合は新規作成を試みる
                event = community.events.order_by('-date').first()
                if event:
                    rule = RecurrenceRule.objects.create(
                        frequency='WEEKLY',
                        interval=2,
                        custom_rule='biweekly_B'
                    )
                    event.recurrence_rule = rule
                    event.is_recurring_master = True
                    event.save()
                    print(f"✓ {name} - 新規RecurrenceRuleを作成")
                else:
                    print(f"✗ {name} - イベントが見つかりません")
        except Community.DoesNotExist:
            print(f"✗ {name} - コミュニティが見つかりません")
    
    print("\n更新が完了しました")

if __name__ == '__main__':
    update_remaining_biweekly_patterns()