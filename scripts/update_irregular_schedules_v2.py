#!/usr/bin/env python
"""イレギュラーな開催周期をcustom_ruleに設定してイベントを生成（修正版）"""

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

# イレギュラーな開催周期の定義
IRREGULAR_SCHEDULES = {
    # ID: (frequency, custom_rule, interval, week_of_month)
    52: ('OTHER', '2～3週間に1度', None, None),
    56: ('WEEKLY', '隔週', 2, None),
    35: ('OTHER', '毎月11日', None, None),
    60: ('OTHER', '毎月8のつく日（8日、18日、28日）', None, None),
    51: ('OTHER', '毎月', None, None),
    62: ('MONTHLY_BY_WEEK', '毎月第2火曜', 1, 2),
    53: ('OTHER', '月1回', None, None),
    42: ('OTHER', 'セミナー会：毎月第一月曜日、雑談会：毎月第三水曜日', None, None),
    36: ('OTHER', 'ABC開催日（主に土曜日・稀に日曜日）', None, None),
    46: ('OTHER', '月1回ペース、不定期', None, None),
    28: ('OTHER', '最終土曜日', None, None),
    47: ('OTHER', '月3回、10日・20日・30日開催', None, None),
    38: ('WEEKLY', '毎週', 1, None),
    12: ('MONTHLY_BY_WEEK', '第3火曜日', 1, 3),
    57: ('MONTHLY_BY_WEEK', '毎月第4月曜', 1, 4),
    71: ('OTHER', '隔週（第1・第3土曜日）', None, None),
    16: ('WEEKLY', '隔週水曜日', 2, None),
    3: ('WEEKLY', '隔週', 2, None),
    54: ('WEEKLY', '隔週', 2, None),
    18: ('WEEKLY', '隔週木曜日', 2, None),
    29: ('OTHER', '第1土曜日または第2土曜日', None, None),
    49: ('MONTHLY_BY_WEEK', '毎月第4水曜日', 1, 4),
    32: ('OTHER', '定例開催は最終土曜日、その他不定期', None, None),
    65: ('OTHER', '毎月第二・四木曜日', None, None),
    50: ('OTHER', '月1', None, None),
    21: ('OTHER', '第一木曜日、第三木曜日', None, None),
    69: ('OTHER', '不定期（月に2、3回程度）', None, None),
    77: ('OTHER', '毎月奇数週（第1・3・5週）水曜日', None, None),
    2: ('MONTHLY_BY_WEEK', '第2日曜日', 1, 2),
    44: ('OTHER', '冬は毎週、春から秋は月1回', None, None),
    39: ('WEEKLY', '隔週月曜日', 2, None),
    25: ('OTHER', '第1土曜日、第3土曜日', None, None),
    40: ('OTHER', '月2回ベース、不定期', None, None),
}

def update_irregular_schedules_v2():
    """イレギュラーな開催周期を更新してイベントを生成（修正版）"""
    
    print("=== イレギュラーな開催周期の更新 (v2) ===\n")
    
    service = RecurrenceService()
    today = timezone.now().date()
    
    total_updated = 0
    total_events_created = 0
    
    for community_id, (frequency, custom_rule, interval, week_of_month) in IRREGULAR_SCHEDULES.items():
        try:
            community = Community.objects.get(id=community_id)
            
            # 既存のマスターイベントを確認
            master_event = community.events.filter(is_recurring_master=True).first()
            
            if not master_event:
                # 最新のイベントを取得してマスターイベントとして設定
                latest_event = community.events.order_by('-date').first()
                if latest_event:
                    # 既存のイベントをマスターイベントに昇格
                    latest_event.is_recurring_master = True
                    latest_event.save()
                    master_event = latest_event
                else:
                    print(f"⚠️ {community.name} (ID: {community_id}): イベントが存在しません")
                    continue
            
            # RecurrenceRuleを更新または作成
            if master_event.recurrence_rule:
                rule = master_event.recurrence_rule
            else:
                rule = RecurrenceRule()
            
            # ルールを更新
            rule.frequency = frequency
            rule.custom_rule = custom_rule
            rule.interval = interval or 1
            
            if week_of_month:
                rule.week_of_month = week_of_month
            
            # start_dateを設定（未設定の場合）
            if not rule.start_date:
                rule.start_date = master_event.date
            
            rule.save()
            
            # マスターイベントにルールを関連付け
            if not master_event.recurrence_rule:
                master_event.recurrence_rule = rule
                master_event.save()
            
            print(f"\n✓ {community.name} (ID: {community_id}):")
            print(f"  周期: {custom_rule}")
            print(f"  頻度: {frequency}")
            print(f"  間隔: {rule.interval}")
            if week_of_month:
                print(f"  第N週: {week_of_month}")
            
            # 既存の未来イベントを削除
            with transaction.atomic():
                future_events = Event.objects.filter(
                    community=community,
                    date__gt=today,
                    is_recurring_master=False
                )
                deleted_count = future_events.count()
                future_events.delete()
                
                # 新しいイベントを生成（3ヶ月分）
                # OTHERタイプはLLMを使うため、一旦スキップ
                if frequency != 'OTHER':
                    dates = service.generate_dates(
                        rule=rule,
                        base_date=today,
                        base_time=master_event.start_time,
                        months=3
                    )
                    
                    created_count = 0
                    for date in dates:
                        if date > today:
                            # 既存チェック
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
                    
                    print(f"  削除: {deleted_count}件")
                    print(f"  作成: {created_count}件")
                    
                    # 生成されたイベントの日付を表示
                    if created_count > 0:
                        next_events = Event.objects.filter(
                            community=community,
                            date__gt=today
                        ).order_by('date')[:5]
                        
                        dates_str = ', '.join(e.date.strftime('%Y-%m-%d') for e in next_events)
                        print(f"  次回開催: {dates_str}")
                    
                    total_events_created += created_count
                else:
                    print(f"  削除: {deleted_count}件")
                    print(f"  ※ カスタムルール（OTHER）のため手動でイベント作成が必要")
                
                total_updated += 1
            
        except Community.DoesNotExist:
            print(f"\n⚠️ コミュニティID {community_id} が見つかりません")
        except Exception as e:
            print(f"\n✗ コミュニティID {community_id} でエラー: {e}")
    
    print(f"\n=== 更新完了 ===")
    print(f"更新したコミュニティ: {total_updated}件")
    print(f"作成したイベント: {total_events_created}件")
    
    # OTHERタイプのコミュニティ一覧を表示
    print("\n=== カスタムルール（OTHER）のコミュニティ ===")
    print("以下のコミュニティは手動でイベント作成が必要です：\n")
    
    for community_id, (frequency, custom_rule, _, _) in IRREGULAR_SCHEDULES.items():
        if frequency == 'OTHER':
            try:
                community = Community.objects.get(id=community_id)
                print(f"- {community.name}: {custom_rule}")
            except Community.DoesNotExist:
                pass

if __name__ == '__main__':
    update_irregular_schedules_v2()