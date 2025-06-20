#!/usr/bin/env python
"""CSVファイルの分析結果に基づいてRecurrenceRuleを更新"""

import os
import sys
import django

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from event.models import RecurrenceRule, Event
from community.models import Community
from datetime import date

def update_recurrence_patterns():
    """開催周期パターンを更新"""
    
    # 1. 株式投資座談会を隔週に更新（コミュニティID: 42）
    try:
        # Eventモデルを通じてRecurrenceRuleを取得
        stock_community = Community.objects.get(id=42)
        stock_event = stock_community.events.filter(is_recurring_master=True).first()
        if stock_event and stock_event.recurrence_rule:
            stock_rule = stock_event.recurrence_rule
            stock_rule.frequency = 'WEEKLY'
            stock_rule.interval = 2
            stock_rule.custom_rule = 'biweekly_B'  # 7月1日が月曜日で第1週なので、次回7月7日は第2週
            stock_rule.save()
            print(f"✓ 株式投資座談会を隔週（偶数週）に更新しました")
        else:
            print("✗ 株式投資座談会のRecurrenceRuleが見つかりません")
    except Community.DoesNotExist:
        print("✗ 株式投資座談会のコミュニティが見つかりません")
    
    # 2. 第1・3週開催のコミュニティ
    biweekly_a_names = [
        'CS集会', 'ITインフラ集会', 'OSS集会', '「計算と自然」集会',
        '仮想学生集会', '個人開発集会', '分解技術集会', '妖怪好き交流所',
        '量子力学のたわいのない雑談の会'
    ]
    
    for name in biweekly_a_names:
        try:
            community = Community.objects.get(name=name)
            event = community.events.filter(is_recurring_master=True).first()
            if event and event.recurrence_rule:
                rule = event.recurrence_rule
                rule.frequency = 'WEEKLY'
                rule.interval = 2
                rule.custom_rule = 'biweekly_A'
                rule.save()
                print(f"✓ {name}を隔週（第1・3週）に更新しました")
            else:
                print(f"✗ {name}のRecurrenceRuleが見つかりません")
        except Community.DoesNotExist:
            print(f"✗ {name}のコミュニティが見つかりません")
    
    # 3. 第2・4週開催のコミュニティ
    biweekly_b_names = [
        'AI集会テックWeek', 'C# Tokyo もくもく会', 'データサイエンティスト集会',
        'バックエンド集会', 'VRChat物理学集会', 'VRCHoudini勉強会',
        'ゲーム開発集会Ⅲ', '分散SNS集会', '社会科学集会', '論文紹介集会'
    ]
    
    for name in biweekly_b_names:
        try:
            community = Community.objects.get(name=name)
            event = community.events.filter(is_recurring_master=True).first()
            if event and event.recurrence_rule:
                rule = event.recurrence_rule
                rule.frequency = 'WEEKLY'
                rule.interval = 2
                rule.custom_rule = 'biweekly_B'
                rule.save()
                print(f"✓ {name}を隔週（第2・4週）に更新しました")
            else:
                print(f"✗ {name}のRecurrenceRuleが見つかりません")
        except Community.DoesNotExist:
            print(f"✗ {name}のコミュニティが見つかりません")
    
    # 4. 毎週開催のコミュニティ
    weekly_names = ['ML集会', '電子工作']
    
    for name in weekly_names:
        try:
            community = Community.objects.get(name=name)
            event = community.events.filter(is_recurring_master=True).first()
            if event and event.recurrence_rule:
                rule = event.recurrence_rule
                rule.frequency = 'WEEKLY'
                rule.interval = 1
                rule.custom_rule = ''
                rule.save()
                print(f"✓ {name}を毎週開催に更新しました")
            else:
                print(f"✗ {name}のRecurrenceRuleが見つかりません")
        except Community.DoesNotExist:
            print(f"✗ {name}のコミュニティが見つかりません")
    
    # 5. 月1回開催のコミュニティ
    monthly_names = ['VRC Blender集会', '化学のおはなし会']
    
    for name in monthly_names:
        try:
            community = Community.objects.get(name=name)
            event = community.events.filter(is_recurring_master=True).first()
            if event and event.recurrence_rule:
                rule = event.recurrence_rule
                rule.frequency = 'MONTHLY_BY_WEEK'
                rule.interval = 1
                rule.custom_rule = ''
                rule.save()
                print(f"✓ {name}を月1回開催に更新しました")
            else:
                print(f"✗ {name}のRecurrenceRuleが見つかりません")
        except Community.DoesNotExist:
            print(f"✗ {name}のコミュニティが見つかりません")
    
    print("\n更新が完了しました")

if __name__ == '__main__':
    update_recurrence_patterns()