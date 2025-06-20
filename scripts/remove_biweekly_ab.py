#!/usr/bin/env python
"""RecurrenceRuleのcustom_ruleからbiweekly_A/biweekly_Bを削除"""

import os
import sys
import django

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from event.models import RecurrenceRule

def remove_biweekly_ab():
    """custom_ruleからbiweekly_A/biweekly_Bを削除"""
    
    print("=== biweekly_A/biweekly_B の削除 ===\n")
    
    # biweekly_A または biweekly_B を含むルールを取得
    rules = RecurrenceRule.objects.filter(
        custom_rule__icontains='biweekly'
    )
    
    print(f"処理対象: {rules.count()}件\n")
    
    updated_count = 0
    
    for rule in rules:
        old_custom_rule = rule.custom_rule
        new_custom_rule = rule.custom_rule
        
        # biweekly_A, biweekly_B を削除
        new_custom_rule = new_custom_rule.replace('biweekly_A', '').strip()
        new_custom_rule = new_custom_rule.replace('biweekly_B', '').strip()
        
        # 空になった場合はNullに設定
        if not new_custom_rule:
            new_custom_rule = None
        
        if old_custom_rule != new_custom_rule:
            rule.custom_rule = new_custom_rule
            rule.save()
            
            # 関連するイベントを取得
            event = rule.event_set.filter(is_recurring_master=True).first()
            if event:
                print(f"✓ {event.community.name}:")
                print(f"  変更前: {old_custom_rule}")
                print(f"  変更後: {new_custom_rule or '(空)'}")
                print(f"  start_date: {rule.start_date}")
                updated_count += 1
    
    print(f"\n=== 更新完了 ===")
    print(f"更新件数: {updated_count}件")
    
    # 確認
    print("\n=== 残存チェック ===")
    remaining = RecurrenceRule.objects.filter(
        custom_rule__icontains='biweekly'
    ).count()
    
    if remaining > 0:
        print(f"⚠️ まだ {remaining}件のルールにbiweeklyが含まれています")
    else:
        print("✓ すべてのbiweekly_A/biweekly_Bが削除されました")

if __name__ == '__main__':
    remove_biweekly_ab()