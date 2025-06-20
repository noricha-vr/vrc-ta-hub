#!/usr/bin/env python
"""イレギュラーな開催周期をcustom_ruleに設定してイベントを生成"""

import os
import sys
import django
import csv
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
    # ID: (frequency, custom_rule)
    52: ('OTHER', '2～3週間に1度'),  # C# Tokyo もくもく会
    56: ('OTHER', '隔週'),  # ITエンジニア キャリア相談・雑談集会
    35: ('OTHER', '毎月11日'),  # UIUXデザイン集会
    60: ('OTHER', '毎月8のつく日（8日、18日、28日）'),  # VRC Bio Journal RTA
    51: ('OTHER', '毎月'),  # VRC MED J SALON
    62: ('MONTHLY_BY_WEEK', '毎月第2火曜'),  # VRChatデザイン集会
    53: ('OTHER', '月1回'),  # VRC微分音集会
    42: ('OTHER', 'セミナー会：毎月第一月曜日、雑談会：毎月第三水曜日'),  # VRC歴史集会
    36: ('OTHER', 'ABC開催日（主に土曜日・稀に日曜日）'),  # VRC競プロ部
    46: ('OTHER', '月1回ペース、不定期'),  # VRアカデミア集会
    28: ('OTHER', '最終土曜日'),  # VRホビーロボット集会
    47: ('OTHER', '月3回、10日・20日・30日開催'),  # VR研究Cafe
    38: ('WEEKLY', '毎週'),  # VR酔い訓練集会
    12: ('MONTHLY_BY_WEEK', '第3火曜日'),  # WEB フロントエンド エンジニア集会
    57: ('MONTHLY_BY_WEEK', '毎月第4月曜'),  # XR開発者集会
    71: ('OTHER', '隔週（第1・第3土曜日）'),  # 「計算と自然」集会
    16: ('OTHER', '隔週水曜日'),  # アバター改変なんもわからん集会
    3: ('OTHER', '隔週'),  # ゲーム開発集会Ⅲ
    54: ('OTHER', '隔週'),  # セキュリティ集会 in VRChat
    18: ('OTHER', '隔週木曜日'),  # データサイエンティスト集会
    29: ('OTHER', '第1土曜日または第2土曜日'),  # バックエンド集会
    49: ('MONTHLY_BY_WEEK', '毎月第4水曜日'),  # ブラックホール集会
    32: ('OTHER', '定例開催は最終土曜日、その他不定期'),  # マネジメント集会
    65: ('OTHER', '毎月第二・四木曜日'),  # 仮想学生集会
    50: ('OTHER', '月1'),  # 分散システム集会
    21: ('OTHER', '第一木曜日、第三木曜日'),  # 分解技術集会
    69: ('OTHER', '不定期（月に2、3回程度）'),  # 天文仮想研究所VSP
    77: ('OTHER', '毎月奇数週（第1・3・5週）水曜日'),  # 妖怪好き交流所『怪し火-AYASHIBI-』
    2: ('MONTHLY_BY_WEEK', '第2日曜日'),  # 技術オタク交流会
    44: ('OTHER', '冬は毎週、春から秋は月1回'),  # 昆虫集会
    39: ('OTHER', '隔週月曜日'),  # 論文紹介集会
    25: ('OTHER', '第1土曜日、第3土曜日'),  # 量子力学のたわいのない雑談の会
    40: ('OTHER', '月2回ベース、不定期'),  # 黒猫雑学カフェ
}

def update_irregular_schedules():
    """イレギュラーな開催周期を更新してイベントを生成"""
    
    print("=== イレギュラーな開催周期の更新 ===\n")
    
    service = RecurrenceService()
    today = timezone.now().date()
    
    total_updated = 0
    total_events_created = 0
    
    for community_id, (frequency, custom_rule) in IRREGULAR_SCHEDULES.items():
        try:
            community = Community.objects.get(id=community_id)
            
            # マスターイベントを取得または作成
            master_event = community.events.filter(is_recurring_master=True).first()
            
            if not master_event:
                # 最新のイベントから情報を取得
                latest_event = community.events.order_by('-date').first()
                if latest_event:
                    master_event = Event.objects.create(
                        community=community,
                        date=latest_event.date,
                        start_time=latest_event.start_time,
                        duration=latest_event.duration,
                        weekday=latest_event.weekday,
                        is_recurring_master=True
                    )
                else:
                    # デフォルト値で作成
                    master_event = Event.objects.create(
                        community=community,
                        date=today,
                        start_time=community.start_time or datetime.strptime('21:00', '%H:%M').time(),
                        duration=community.duration or 60,
                        weekday=today.strftime('%a').upper()[:3],
                        is_recurring_master=True
                    )
            
            # RecurrenceRuleを更新または作成
            if master_event.recurrence_rule:
                rule = master_event.recurrence_rule
                rule.frequency = frequency
                rule.custom_rule = custom_rule
                # MONTHLY_BY_WEEKの場合は追加設定
                if frequency == 'MONTHLY_BY_WEEK':
                    rule.interval = 1
                    if '第2' in custom_rule:
                        rule.week_of_month = 2
                    elif '第3' in custom_rule:
                        rule.week_of_month = 3
                    elif '第4' in custom_rule:
                        rule.week_of_month = 4
                    else:
                        rule.week_of_month = 1
                rule.save()
            else:
                rule = RecurrenceRule.objects.create(
                    frequency=frequency,
                    custom_rule=custom_rule,
                    interval=1,
                    start_date=master_event.date
                )
                if frequency == 'MONTHLY_BY_WEEK':
                    if '第2' in custom_rule:
                        rule.week_of_month = 2
                    elif '第3' in custom_rule:
                        rule.week_of_month = 3
                    elif '第4' in custom_rule:
                        rule.week_of_month = 4
                    else:
                        rule.week_of_month = 1
                    rule.save()
                master_event.recurrence_rule = rule
                master_event.save()
            
            print(f"\n✓ {community.name} (ID: {community_id}):")
            print(f"  周期: {custom_rule}")
            print(f"  頻度: {frequency}")
            
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
                dates = service.generate_dates(
                    rule=rule,
                    base_date=today,
                    base_time=master_event.start_time,
                    months=3
                )
                
                created_count = 0
                for date in dates:
                    if date > today:
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
                
                total_updated += 1
                total_events_created += created_count
            
        except Community.DoesNotExist:
            print(f"\n⚠️ コミュニティID {community_id} が見つかりません")
        except Exception as e:
            print(f"\n✗ コミュニティID {community_id} でエラー: {e}")
            import traceback
            traceback.print_exc()
    
    print(f"\n=== 更新完了 ===")
    print(f"更新したコミュニティ: {total_updated}件")
    print(f"作成したイベント: {total_events_created}件")

if __name__ == '__main__':
    update_irregular_schedules()