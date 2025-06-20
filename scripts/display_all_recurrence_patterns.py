#!/usr/bin/env python
"""すべてのコミュニティの開催周期パターンを表示"""

import os
import sys
import django

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from event.models import RecurrenceRule, Event
from community.models import Community
from collections import defaultdict

def display_all_recurrence_patterns():
    """すべてのコミュニティの開催周期パターンを表示"""
    
    # パターンごとにコミュニティを分類
    patterns = defaultdict(list)
    no_rule = []
    
    communities = Community.objects.filter(status='approved').order_by('name')
    
    for community in communities:
        # RecurrenceRuleを持つイベントを探す
        event = community.events.filter(is_recurring_master=True).first()
        
        if event and event.recurrence_rule:
            rule = event.recurrence_rule
            
            # パターンを判定
            if rule.frequency == 'WEEKLY' and rule.interval == 1:
                pattern = '毎週'
            elif rule.frequency == 'WEEKLY' and rule.interval == 2:
                if rule.custom_rule == 'biweekly_A':
                    pattern = '隔週（第1・3週）'
                elif rule.custom_rule == 'biweekly_B':
                    pattern = '隔週（第2・4週）'
                else:
                    pattern = '隔週（グループ未確定）'
            elif rule.frequency == 'MONTHLY_BY_WEEK':
                pattern = '月1回'
            elif rule.frequency == 'MONTHLY_BY_DATE':
                pattern = '月1回（日付指定）'
            elif rule.frequency == 'OTHER':
                pattern = f'その他（{rule.custom_rule}）'
            else:
                pattern = 'その他'
            
            # 曜日情報を追加
            weekday_dict = {
                'Mon': '月曜日', 'Tue': '火曜日', 'Wed': '水曜日',
                'Thu': '木曜日', 'Fri': '金曜日', 'Sat': '土曜日', 'Sun': '日曜日'
            }
            weekdays = community.weekdays if isinstance(community.weekdays, list) else eval(community.weekdays) if community.weekdays else []
            weekday_str = '・'.join([weekday_dict.get(w, w) for w in weekdays])
            
            patterns[pattern].append(f"{community.name} - {pattern}{weekday_str}")
        else:
            # RecurrenceRuleがない場合
            weekday_dict = {
                'Mon': '月曜日', 'Tue': '火曜日', 'Wed': '水曜日',
                'Thu': '木曜日', 'Fri': '金曜日', 'Sat': '土曜日', 'Sun': '日曜日'
            }
            weekdays = community.weekdays if isinstance(community.weekdays, list) else eval(community.weekdays) if community.weekdays else []
            weekday_str = '・'.join([weekday_dict.get(w, w) for w in weekdays])
            
            # frequencyフィールドから判定
            if '毎週' in community.frequency:
                patterns['毎週'].append(f"{community.name} - 毎週{weekday_str}")
            elif '隔週' in community.frequency:
                patterns['隔週（グループ未確定）'].append(f"{community.name} - 隔週{weekday_str}")
            elif '第' in community.frequency:
                patterns['月1回'].append(f"{community.name} - {community.frequency}")
            else:
                no_rule.append(f"{community.name} - {community.frequency}")
    
    # 結果を表示
    print("=== VRC技術学術ハブ 集会開催周期一覧 ===\n")
    
    # パターンの順序を定義
    pattern_order = [
        '毎週',
        '隔週（第1・3週）',
        '隔週（第2・4週）',
        '隔週（グループ未確定）',
        '月1回',
        '月1回（日付指定）',
        'その他'
    ]
    
    for pattern in pattern_order:
        if pattern in patterns:
            print(f"## {pattern}開催（{len(patterns[pattern])}コミュニティ）")
            for community in sorted(patterns[pattern]):
                print(f"- {community}")
            print()
    
    # その他のパターン
    for pattern, communities in patterns.items():
        if pattern not in pattern_order:
            print(f"## {pattern}（{len(communities)}コミュニティ）")
            for community in sorted(communities):
                print(f"- {community}")
            print()
    
    if no_rule:
        print(f"## RecurrenceRuleなし（{len(no_rule)}コミュニティ）")
        for community in sorted(no_rule):
            print(f"- {community}")
        print()
    
    # 統計
    total = sum(len(communities) for communities in patterns.values()) + len(no_rule)
    print(f"\n合計: {total}コミュニティ")

if __name__ == '__main__':
    display_all_recurrence_patterns()