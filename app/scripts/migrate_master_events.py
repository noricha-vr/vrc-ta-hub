#!/usr/bin/env python
"""マスターイベントを最初の実際の開催日として移行"""

import os
import sys
import django
from datetime import datetime

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from django.db import transaction
from django.utils import timezone
from community.models import Community
from event.models import Event, RecurrenceRule


def migrate_master_events(dry_run=True):
    """マスターイベントの日付を最初の実際の開催日に移行"""
    print('=== マスターイベントの移行 ===')
    print(f'DRY RUN: {dry_run}\n')
    
    today = timezone.now().date()
    
    # マスターイベントを取得
    master_events = Event.objects.filter(
        is_recurring_master=True
    ).select_related('community', 'recurrence_rule').order_by('date')
    
    print(f'マスターイベント総数: {master_events.count()}件\n')
    
    stats = {
        'updated': 0,
        'skipped': 0,
        'errors': 0
    }
    
    with transaction.atomic():
        for master in master_events:
            try:
                # このマスターから生成された最初のインスタンスを取得
                first_instance = Event.objects.filter(
                    recurring_master=master
                ).order_by('date').first()
                
                if not first_instance:
                    print(f'[スキップ] {master.community.name}: インスタンスが存在しない')
                    stats['skipped'] += 1
                    continue
                
                # マスターイベントの日付が未来の場合はスキップ
                if master.date >= today:
                    print(f'[スキップ] {master.community.name}: マスターは既に未来の日付 ({master.date})')
                    stats['skipped'] += 1
                    continue
                
                # 最初のインスタンスの日付が過去の場合もスキップ
                if first_instance.date < today:
                    print(f'[スキップ] {master.community.name}: 最初のインスタンスも過去 ({first_instance.date})')
                    stats['skipped'] += 1
                    continue
                
                print(f'\n{master.community.name}:')
                print(f'  現在のマスター日付: {master.date}')
                print(f'  最初のインスタンス日付: {first_instance.date}')
                
                if not dry_run:
                    # 最初のインスタンスを先に削除（重複を避けるため）
                    first_instance_date = first_instance.date
                    first_instance.delete()
                    
                    # マスターイベントの日付を更新
                    master.date = first_instance_date
                    master.save(update_fields=['date'])
                    
                    # RecurrenceRuleの開始日も更新
                    if master.recurrence_rule:
                        master.recurrence_rule.start_date = first_instance_date
                        master.recurrence_rule.save(update_fields=['start_date'])
                
                print(f'  → マスターを {first_instance.date} に更新')
                print(f'  → 最初のインスタンスを削除')
                stats['updated'] += 1
                
            except Exception as e:
                print(f'[エラー] {master.community.name}: {e}')
                stats['errors'] += 1
                if not dry_run:
                    raise
    
    print(f'\n=== 移行結果 ===')
    print(f'更新: {stats["updated"]}件')
    print(f'スキップ: {stats["skipped"]}件')
    print(f'エラー: {stats["errors"]}件')
    
    if dry_run:
        print('\n※ DRY RUNモードです。実際の変更は行われていません。')
        print('実際に移行を実行するには --apply オプションを使用してください。')


def verify_migration():
    """移行後の状態を確認"""
    print('\n=== 移行後の確認 ===\n')
    
    today = timezone.now().date()
    
    # 未来のマスターイベントを確認
    future_masters = Event.objects.filter(
        is_recurring_master=True,
        date__gte=today
    ).select_related('community').order_by('date')[:10]
    
    print(f'未来のマスターイベント（最初の10件）:')
    for master in future_masters:
        # 生成されたインスタンスの最初の日付を確認
        first_instance = Event.objects.filter(
            recurring_master=master
        ).order_by('date').first()
        
        if first_instance and first_instance.date <= master.date:
            print(f'  ⚠️ {master.community.name}: マスター={master.date}, 最初のインスタンス={first_instance.date}')
        else:
            print(f'  ✓ {master.community.name}: マスター={master.date}')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='マスターイベントを最初の実際の開催日に移行')
    parser.add_argument('--apply', action='store_true', help='実際に移行を実行')
    parser.add_argument('--verify', action='store_true', help='移行後の確認のみ実行')
    args = parser.parse_args()
    
    if args.verify:
        verify_migration()
    else:
        migrate_master_events(dry_run=not args.apply)