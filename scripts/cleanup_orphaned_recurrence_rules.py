#!/usr/bin/env python
"""
孤立したRecurrenceRule（コミュニティが紐づいていない）を削除するスクリプト
"""
import os
import sys
import django

# Djangoプロジェクトのパスを追加
sys.path.append('/app/website')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from event.models import RecurrenceRule
from django.db.models import Q


def cleanup_orphaned_recurrence_rules():
    """コミュニティが紐づいていないRecurrenceRuleを削除"""
    
    # コミュニティが紐づいていないRecurrenceRuleを検索
    orphaned_rules = RecurrenceRule.objects.filter(
        Q(community__isnull=True) | Q(community__status='rejected')
    )
    
    if not orphaned_rules.exists():
        print("孤立したRecurrenceRuleは見つかりませんでした。")
        return
    
    print(f"孤立したRecurrenceRuleが{orphaned_rules.count()}件見つかりました：")
    
    for rule in orphaned_rules:
        print(f"\nID: {rule.id}")
        print(f"  頻度: {rule.get_frequency_display()}")
        print(f"  カスタムルール: {rule.custom_rule or 'なし'}")
        print(f"  作成日: {rule.created_at}")
        
        # 関連するマスターイベントを確認
        master_events = rule.events.filter(is_recurring_master=True)
        if master_events.exists():
            print(f"  関連マスターイベント数: {master_events.count()}")
            for event in master_events:
                print(f"    - {event.name} (ID: {event.id})")
    
    # 確認
    confirm = input("\nこれらのRecurrenceRuleを削除しますか？ (yes/no): ")
    
    if confirm.lower() == 'yes':
        deleted_count = orphaned_rules.count()
        orphaned_rules.delete()
        print(f"\n{deleted_count}件のRecurrenceRuleを削除しました。")
    else:
        print("\n削除をキャンセルしました。")


if __name__ == "__main__":
    cleanup_orphaned_recurrence_rules()