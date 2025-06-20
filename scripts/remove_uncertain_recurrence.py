#!/usr/bin/env python
"""不確定な開催周期のRecurrenceRuleを削除"""

import os
import sys
import django

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from django.utils import timezone
from django.db import transaction
from event.models import Event, RecurrenceRule
from community.models import Community

# 不確定な開催周期のパターン
UNCERTAIN_PATTERNS = [
    '第1土曜日または第2土曜日',
    '2～3週間に1度',
    '月2回ベース、不定期',
    '不定期',
    '月1回ペース、不定期',
    'だいたい隔週',
    '定例開催は最終土曜日、その他不定期',
    '冬は毎週、春から秋は月1回',
    '不定期（月に2、3回程度）',
    'ABC開催日（主に土曜日・稀に日曜日）',
    '不定期?',
    '基本的に隔週',
]

def remove_uncertain_recurrence():
    """不確定な開催周期のRecurrenceRuleを削除"""
    
    print("=== 不確定な開催周期の削除 ===\n")
    
    today = timezone.now().date()
    
    # 削除対象のルールを検索
    rules_to_delete = []
    
    # custom_ruleに不確定パターンが含まれるものを検索
    for pattern in UNCERTAIN_PATTERNS:
        rules = RecurrenceRule.objects.filter(custom_rule__icontains=pattern)
        for rule in rules:
            if rule not in rules_to_delete:
                rules_to_delete.append(rule)
    
    # 開催周期フィールドから不確定パターンを検索
    communities = Community.objects.all()
    for community in communities:
        if community.frequency in UNCERTAIN_PATTERNS:
            # このコミュニティのRecurrenceRuleを取得
            master_events = community.events.filter(is_recurring_master=True)
            for event in master_events:
                if event.recurrence_rule and event.recurrence_rule not in rules_to_delete:
                    rules_to_delete.append(event.recurrence_rule)
    
    print(f"削除対象のRecurrenceRule: {len(rules_to_delete)}件\n")
    
    # 削除処理
    total_events_deleted = 0
    
    for rule in rules_to_delete:
        # このルールに関連するイベントを取得
        master_events = Event.objects.filter(recurrence_rule=rule, is_recurring_master=True)
        
        for master_event in master_events:
            community = master_event.community
            
            print(f"\n{community.name}:")
            print(f"  開催周期: {community.frequency}")
            print(f"  カスタムルール: {rule.custom_rule}")
            
            with transaction.atomic():
                # 未来の自動生成イベントを削除
                future_events = Event.objects.filter(
                    recurring_master=master_event,
                    date__gt=today
                )
                deleted_count = future_events.count()
                future_events.delete()
                
                # マスターイベントからRecurrenceRuleの関連を削除
                master_event.recurrence_rule = None
                master_event.save()
                
                print(f"  削除したイベント: {deleted_count}件")
                print(f"  RecurrenceRuleとの関連を解除")
                
                total_events_deleted += deleted_count
        
        # RecurrenceRuleを削除
        rule.delete()
        print(f"  RecurrenceRuleを削除")
    
    # 不確定パターンだがRecurrenceRuleを持たないコミュニティも確認
    print("\n=== RecurrenceRuleを持たない不確定パターンのコミュニティ ===")
    
    for community in communities:
        if community.frequency in UNCERTAIN_PATTERNS:
            has_rule = community.events.filter(
                is_recurring_master=True,
                recurrence_rule__isnull=False
            ).exists()
            
            if not has_rule:
                print(f"- {community.name}: {community.frequency}")
    
    print(f"\n=== 削除完了 ===")
    print(f"削除したRecurrenceRule: {len(rules_to_delete)}件")
    print(f"削除したイベント: {total_events_deleted}件")
    
    # 残っている不確定パターンを確認
    print("\n=== 残存チェック ===")
    
    remaining_count = 0
    for pattern in UNCERTAIN_PATTERNS:
        count = RecurrenceRule.objects.filter(custom_rule__icontains=pattern).count()
        if count > 0:
            print(f"⚠️ '{pattern}' を含むルール: {count}件")
            remaining_count += count
    
    if remaining_count == 0:
        print("✓ すべての不確定パターンが削除されました")

if __name__ == '__main__':
    remove_uncertain_recurrence()