#!/usr/bin/env python
"""CSVファイルの分析結果に基づいて隔週パターンを更新"""

import os
import sys
import django

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from event.models import RecurrenceRule, Event
from community.models import Community

def update_biweekly_patterns():
    """隔週パターンを一括更新"""
    
    # 奇数週開催グループ（biweekly_A）
    biweekly_a_communities = [
        'AI集会ゆる雑談Week',
        'OSS集会（オープンソースソフトウェア集会）',
        'アバターホビー集会',
        'ゲーム開発集会Ⅲ',
    ]
    
    # 偶数週開催グループ（biweekly_B）
    biweekly_b_communities = [
        'ITエンジニア キャリア相談・雑談集会',
        'データサイエンティスト集会',
        'だいたいで分かる政治経済',
    ]
    
    # 不規則開催（custom_ruleを空にする）
    irregular_communities = [
        '「計算と自然」集会',
        'セキュリティ集会 in VRChat',
        '妖怪好き交流所『怪し火-AYASHIBI-』',
        '論文紹介集会',
    ]
    
    print("=== 隔週パターンの一括更新 ===\n")
    
    # 奇数週グループの更新
    print("## 奇数週開催グループ（第1・3週）")
    for name in biweekly_a_communities:
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
                print(f"✗ {name} - RecurrenceRuleが見つかりません")
        except Community.DoesNotExist:
            print(f"✗ {name} - コミュニティが見つかりません")
    
    # 偶数週グループの更新
    print("\n## 偶数週開催グループ（第2・4週）")
    for name in biweekly_b_communities:
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
                print(f"✗ {name} - RecurrenceRuleが見つかりません")
        except Community.DoesNotExist:
            print(f"✗ {name} - コミュニティが見つかりません")
    
    # 不規則開催グループの更新
    print("\n## 不規則開催グループ")
    for name in irregular_communities:
        try:
            community = Community.objects.get(name=name)
            event = community.events.filter(is_recurring_master=True).first()
            if event and event.recurrence_rule:
                rule = event.recurrence_rule
                # custom_ruleを空にして不規則パターンであることを示す
                rule.custom_rule = ''
                rule.save()
                print(f"✓ {name} - 不規則開催として設定")
            else:
                print(f"✗ {name} - RecurrenceRuleが見つかりません")
        except Community.DoesNotExist:
            print(f"✗ {name} - コミュニティが見つかりません")
    
    print("\n更新が完了しました")

if __name__ == '__main__':
    update_biweekly_patterns()