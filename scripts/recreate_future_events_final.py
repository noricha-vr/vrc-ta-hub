#!/usr/bin/env python
"""今日以降のイベントを再作成（最終版）"""

import os
import sys
import django
from datetime import datetime, timedelta

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from django.utils import timezone
from django.db import transaction
from event.models import Event, RecurrenceRule
from event.recurrence_service import RecurrenceService
from community.models import Community

def recreate_future_events_final():
    """今日以降のイベントを再作成（最終版）"""
    
    print("=== 今日以降のイベントを再作成 ===\n")
    
    today = timezone.now().date()
    service = RecurrenceService()
    
    # まず、今日以降の自動生成イベントをすべて削除
    print("1. 既存の未来イベントを削除中...")
    
    future_events = Event.objects.filter(
        date__gte=today,
        is_recurring_master=False
    )
    
    deleted_count = future_events.count()
    future_events.delete()
    
    print(f"   削除: {deleted_count}件\n")
    
    # RecurrenceRuleを持つマスターイベントを取得
    print("2. 定期イベントを生成中...\n")
    
    recurring_masters = Event.objects.filter(
        is_recurring_master=True,
        recurrence_rule__isnull=False
    ).select_related('recurrence_rule', 'community')
    
    print(f"   処理対象: {recurring_masters.count()}件のマスターイベント\n")
    
    total_created = 0
    
    for master_event in recurring_masters:
        rule = master_event.recurrence_rule
        community = master_event.community
        
        # OTHERタイプはスキップ（不確定なものは削除済み）
        if rule.frequency == 'OTHER':
            print(f"   ⚠️ {community.name}: カスタムルール（{rule.custom_rule}）のためスキップ")
            continue
        
        with transaction.atomic():
            # 1ヶ月分のイベントを生成
            dates = service.generate_dates(
                rule=rule,
                base_date=today,
                base_time=master_event.start_time,
                months=1
            )
            
            created_count = 0
            for date in dates:
                if date >= today:
                    # 既存チェック（念のため）
                    exists = Event.objects.filter(
                        community=community,
                        date=date,
                        start_time=master_event.start_time
                    ).exists()
                    
                    if not exists:
                        Event.objects.create(
                            community=community,
                            date=date,
                            start_time=master_event.start_time,
                            duration=master_event.duration,
                            weekday=date.strftime('%a').upper()[:3],
                            recurring_master=master_event
                        )
                        created_count += 1
            
            if created_count > 0:
                print(f"   ✓ {community.name}: {created_count}件作成")
                total_created += created_count
    
    # カスタムルール（OTHER）で確定的なものを生成
    print("\n3. カスタムルールの確定的なイベントを生成中...\n")
    
    # generate_custom_events.pyの内容を組み込み
    from dateutil.relativedelta import relativedelta
    
    def generate_custom_dates(rule_text, base_date, months=1):
        """カスタムルールに基づいて日付を生成（確定的なもののみ）"""
        dates = []
        end_date = base_date + relativedelta(months=months)
        
        if '毎月11日' in rule_text:
            current = base_date.replace(day=11)
            if current < base_date:
                current += relativedelta(months=1)
            while current <= end_date:
                dates.append(current)
                current += relativedelta(months=1)
        
        elif '8のつく日' in rule_text:
            current = base_date.replace(day=1)
            while current <= end_date:
                for day in [8, 18, 28]:
                    try:
                        d = current.replace(day=day)
                        if base_date <= d <= end_date:
                            dates.append(d)
                    except ValueError:
                        pass
                current += relativedelta(months=1)
        
        elif '最終土曜日' in rule_text:
            current = base_date.replace(day=1)
            while current <= end_date:
                next_month = current + relativedelta(months=1)
                last_day = (next_month - timedelta(days=1))
                while last_day.weekday() != 5:
                    last_day -= timedelta(days=1)
                if base_date <= last_day <= end_date:
                    dates.append(last_day)
                current += relativedelta(months=1)
        
        elif '10日・20日・30日' in rule_text or '10/20/30日' in rule_text:
            current = base_date.replace(day=1)
            while current <= end_date:
                for day in [10, 20, 30]:
                    try:
                        d = current.replace(day=day)
                        if base_date <= d <= end_date:
                            dates.append(d)
                    except ValueError:
                        pass
                current += relativedelta(months=1)
        
        elif '第1・第3土曜日' in rule_text or '第1第3週' in rule_text:
            current = base_date.replace(day=1)
            while current <= end_date:
                first_sat = current
                while first_sat.weekday() != 5:
                    first_sat += timedelta(days=1)
                if base_date <= first_sat <= end_date:
                    dates.append(first_sat)
                
                third_sat = first_sat + timedelta(weeks=2)
                if third_sat.month == current.month and base_date <= third_sat <= end_date:
                    dates.append(third_sat)
                
                current += relativedelta(months=1)
        
        elif '第一月曜日' in rule_text and '第三水曜日' in rule_text:
            current = base_date.replace(day=1)
            while current <= end_date:
                first_mon = current
                while first_mon.weekday() != 0:
                    first_mon += timedelta(days=1)
                if base_date <= first_mon <= end_date:
                    dates.append(first_mon)
                
                first_wed = current
                while first_wed.weekday() != 2:
                    first_wed += timedelta(days=1)
                third_wed = first_wed + timedelta(weeks=2)
                if third_wed.month == current.month and base_date <= third_wed <= end_date:
                    dates.append(third_wed)
                
                current += relativedelta(months=1)
        
        elif '第二・四木曜日' in rule_text or '第二・第四木曜日' in rule_text:
            current = base_date.replace(day=1)
            while current <= end_date:
                first_thu = current
                while first_thu.weekday() != 3:
                    first_thu += timedelta(days=1)
                
                second_thu = first_thu + timedelta(weeks=1)
                if second_thu.month == current.month and base_date <= second_thu <= end_date:
                    dates.append(second_thu)
                
                fourth_thu = first_thu + timedelta(weeks=3)
                if fourth_thu.month == current.month and base_date <= fourth_thu <= end_date:
                    dates.append(fourth_thu)
                
                current += relativedelta(months=1)
        
        elif '第一木曜日' in rule_text and '第三木曜日' in rule_text:
            current = base_date.replace(day=1)
            while current <= end_date:
                first_thu = current
                while first_thu.weekday() != 3:
                    first_thu += timedelta(days=1)
                if base_date <= first_thu <= end_date:
                    dates.append(first_thu)
                
                third_thu = first_thu + timedelta(weeks=2)
                if third_thu.month == current.month and base_date <= third_thu <= end_date:
                    dates.append(third_thu)
                
                current += relativedelta(months=1)
        
        elif '奇数週' in rule_text and '水曜日' in rule_text:
            current = base_date.replace(day=1)
            while current <= end_date:
                first_wed = current
                while first_wed.weekday() != 2:
                    first_wed += timedelta(days=1)
                
                for week in [0, 2, 4]:
                    wed = first_wed + timedelta(weeks=week)
                    if wed.month == current.month and base_date <= wed <= end_date:
                        dates.append(wed)
                
                current += relativedelta(months=1)
        
        elif '月1回' in rule_text or '月1' in rule_text or '毎月' in rule_text:
            current = base_date
            while current <= end_date:
                if current >= base_date:
                    dates.append(current)
                current += relativedelta(months=1)
        
        return sorted(dates)
    
    # カスタムルールのマスターイベントを処理
    custom_masters = Event.objects.filter(
        is_recurring_master=True,
        recurrence_rule__frequency='OTHER'
    ).select_related('recurrence_rule', 'community')
    
    custom_created = 0
    
    for master_event in custom_masters:
        rule = master_event.recurrence_rule
        community = master_event.community
        
        # 確定的なパターンのみ処理
        dates = generate_custom_dates(rule.custom_rule, today, months=1)
        
        if dates:
            with transaction.atomic():
                created_count = 0
                for date in dates:
                    exists = Event.objects.filter(
                        community=community,
                        date=date,
                        start_time=master_event.start_time
                    ).exists()
                    
                    if not exists:
                        Event.objects.create(
                            community=community,
                            date=date,
                            start_time=master_event.start_time,
                            duration=master_event.duration,
                            weekday=date.strftime('%a').upper()[:3],
                            recurring_master=master_event
                        )
                        created_count += 1
                
                if created_count > 0:
                    print(f"   ✓ {community.name}: {created_count}件作成")
                    custom_created += created_count
    
    print(f"\n=== 再作成完了 ===")
    print(f"削除: {deleted_count}件")
    print(f"作成（定期）: {total_created}件")
    print(f"作成（カスタム）: {custom_created}件")
    print(f"合計作成: {total_created + custom_created}件")
    
    # 統計情報
    print("\n=== 統計情報 ===")
    
    # 今後1ヶ月のイベント数
    one_month_later = today + relativedelta(months=1)
    upcoming_count = Event.objects.filter(
        date__gte=today,
        date__lte=one_month_later
    ).count()
    
    print(f"今後1ヶ月のイベント数: {upcoming_count}件")
    
    # RecurrenceRuleを持つコミュニティ数
    communities_with_rule = Community.objects.filter(
        events__is_recurring_master=True,
        events__recurrence_rule__isnull=False
    ).distinct().count()
    
    print(f"定期開催のコミュニティ数: {communities_with_rule}件")

if __name__ == '__main__':
    recreate_future_events_final()