#!/usr/bin/env python
"""カスタムルール（OTHER）のイベントを手動生成"""

import os
import sys
import django
from datetime import datetime, timedelta, date
from dateutil.relativedelta import relativedelta

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from django.utils import timezone
from django.db import transaction
from event.models import Event, RecurrenceRule
from community.models import Community

# カスタムルールの日付生成ロジック
def generate_custom_dates(rule_text, base_date, months=1):
    """カスタムルールに基づいて日付を生成"""
    dates = []
    end_date = base_date + relativedelta(months=months)
    
    if '毎月11日' in rule_text:
        # 毎月11日
        current = base_date.replace(day=11)
        if current < base_date:
            current += relativedelta(months=1)
        while current <= end_date:
            dates.append(current)
            current += relativedelta(months=1)
    
    elif '8のつく日' in rule_text:
        # 毎月8日、18日、28日
        current = base_date.replace(day=1)
        while current <= end_date:
            for day in [8, 18, 28]:
                try:
                    d = current.replace(day=day)
                    if base_date <= d <= end_date:
                        dates.append(d)
                except ValueError:
                    pass  # 月末で28日が存在しない場合
            current += relativedelta(months=1)
    
    elif '最終土曜日' in rule_text:
        # 毎月最終土曜日
        current = base_date.replace(day=1)
        while current <= end_date:
            # 月末日を取得
            next_month = current + relativedelta(months=1)
            last_day = (next_month - timedelta(days=1))
            # 最終土曜日を探す
            while last_day.weekday() != 5:  # 5 = Saturday
                last_day -= timedelta(days=1)
            if base_date <= last_day <= end_date:
                dates.append(last_day)
            current += relativedelta(months=1)
    
    elif '10日・20日・30日' in rule_text or '10/20/30日' in rule_text:
        # 毎月10日、20日、30日
        current = base_date.replace(day=1)
        while current <= end_date:
            for day in [10, 20, 30]:
                try:
                    d = current.replace(day=day)
                    if base_date <= d <= end_date:
                        dates.append(d)
                except ValueError:
                    pass  # 2月30日など存在しない日付
            current += relativedelta(months=1)
    
    elif '第1・第3土曜日' in rule_text or '第1第3週' in rule_text:
        # 第1・第3土曜日
        current = base_date.replace(day=1)
        while current <= end_date:
            # 第1土曜日
            first_sat = current
            while first_sat.weekday() != 5:
                first_sat += timedelta(days=1)
            if base_date <= first_sat <= end_date:
                dates.append(first_sat)
            
            # 第3土曜日
            third_sat = first_sat + timedelta(weeks=2)
            if third_sat.month == current.month and base_date <= third_sat <= end_date:
                dates.append(third_sat)
            
            current += relativedelta(months=1)
    
    elif '第一月曜日' in rule_text and '第三水曜日' in rule_text:
        # 第一月曜日と第三水曜日
        current = base_date.replace(day=1)
        while current <= end_date:
            # 第一月曜日
            first_mon = current
            while first_mon.weekday() != 0:  # 0 = Monday
                first_mon += timedelta(days=1)
            if base_date <= first_mon <= end_date:
                dates.append(first_mon)
            
            # 第三水曜日
            first_wed = current
            while first_wed.weekday() != 2:  # 2 = Wednesday
                first_wed += timedelta(days=1)
            third_wed = first_wed + timedelta(weeks=2)
            if third_wed.month == current.month and base_date <= third_wed <= end_date:
                dates.append(third_wed)
            
            current += relativedelta(months=1)
    
    elif '第1土曜日または第2土曜日' in rule_text:
        # 第1土曜日（基本）
        current = base_date.replace(day=1)
        while current <= end_date:
            first_sat = current
            while first_sat.weekday() != 5:
                first_sat += timedelta(days=1)
            if base_date <= first_sat <= end_date:
                dates.append(first_sat)
            current += relativedelta(months=1)
    
    elif '第二・四木曜日' in rule_text or '第二・第四木曜日' in rule_text:
        # 第2・第4木曜日
        current = base_date.replace(day=1)
        while current <= end_date:
            # 第1木曜日を探す
            first_thu = current
            while first_thu.weekday() != 3:  # 3 = Thursday
                first_thu += timedelta(days=1)
            
            # 第2木曜日
            second_thu = first_thu + timedelta(weeks=1)
            if second_thu.month == current.month and base_date <= second_thu <= end_date:
                dates.append(second_thu)
            
            # 第4木曜日
            fourth_thu = first_thu + timedelta(weeks=3)
            if fourth_thu.month == current.month and base_date <= fourth_thu <= end_date:
                dates.append(fourth_thu)
            
            current += relativedelta(months=1)
    
    elif '第一木曜日' in rule_text and '第三木曜日' in rule_text:
        # 第1・第3木曜日
        current = base_date.replace(day=1)
        while current <= end_date:
            # 第1木曜日
            first_thu = current
            while first_thu.weekday() != 3:
                first_thu += timedelta(days=1)
            if base_date <= first_thu <= end_date:
                dates.append(first_thu)
            
            # 第3木曜日
            third_thu = first_thu + timedelta(weeks=2)
            if third_thu.month == current.month and base_date <= third_thu <= end_date:
                dates.append(third_thu)
            
            current += relativedelta(months=1)
    
    elif '奇数週' in rule_text and '水曜日' in rule_text:
        # 第1・3・5水曜日
        current = base_date.replace(day=1)
        while current <= end_date:
            # 第1水曜日を探す
            first_wed = current
            while first_wed.weekday() != 2:  # 2 = Wednesday
                first_wed += timedelta(days=1)
            
            # 奇数週の水曜日
            for week in [0, 2, 4]:  # 第1, 3, 5週
                wed = first_wed + timedelta(weeks=week)
                if wed.month == current.month and base_date <= wed <= end_date:
                    dates.append(wed)
            
            current += relativedelta(months=1)
    
    elif '2～3週間に1度' in rule_text:
        # 2～3週間ごと（2.5週間 = 17日間隔で計算）
        current = base_date
        while current <= end_date:
            if current >= base_date:
                dates.append(current)
            current += timedelta(days=17)
    
    elif '月1回' in rule_text or '月1' in rule_text or '毎月' in rule_text:
        # 月1回（前回開催日と同じ週の同じ曜日）
        current = base_date
        base_weekday = base_date.weekday()  # 基準日の曜日
        base_week = (base_date.day - 1) // 7 + 1  # 基準日が第何週か
        
        while current <= end_date:
            if current >= base_date:
                dates.append(current)
            
            # 次の月の同じ週の同じ曜日を計算
            next_month = current + relativedelta(months=1)
            # 月初めの曜日を取得
            first_day = next_month.replace(day=1)
            # 第1回目の同じ曜日まで何日あるか
            days_to_weekday = (base_weekday - first_day.weekday()) % 7
            # 第n週の同じ曜日
            target_date = first_day + timedelta(days=days_to_weekday + (base_week - 1) * 7)
            
            # 月をまたがってしまった場合は、その月の最後の同じ曜日にする
            if target_date.month != next_month.month:
                target_date -= timedelta(weeks=1)
            
            current = target_date
    
    return sorted(dates)

# カスタムルールのコミュニティ
CUSTOM_COMMUNITIES = {
    52: ('C# Tokyo もくもく会', '2～3週間に1度'),
    35: ('UIUXデザイン集会', '毎月11日'),
    60: ('VRC Bio Journal RTA', '毎月8のつく日（8日、18日、28日）'),
    51: ('VRC MED J SALON', '毎月'),
    53: ('VRC微分音集会', '月1回'),
    42: ('VRC歴史集会', 'セミナー会：毎月第一月曜日、雑談会：毎月第三水曜日'),
    28: ('VRホビーロボット集会', '最終土曜日'),
    47: ('VR研究Cafe', '月3回、10日・20日・30日開催'),
    71: ('「計算と自然」集会', '隔週（第1・第3土曜日）'),
    29: ('バックエンド集会', '第1土曜日または第2土曜日'),
    32: ('マネジメント集会', '定例開催は最終土曜日、その他不定期'),
    65: ('仮想学生集会', '毎月第二・四木曜日'),
    50: ('分散システム集会', '月1'),
    21: ('分解技術集会', '第一木曜日、第三木曜日'),
    77: ('妖怪好き交流所『怪し火-AYASHIBI-』', '毎月奇数週（第1・3・5週）水曜日'),
    25: ('量子力学のたわいのない雑談の会', '第1土曜日、第3土曜日'),
    40: ('黒猫雑学カフェ', '月2回ベース、不定期'),
}

def generate_custom_events():
    """カスタムルールのイベントを生成"""
    
    print("=== カスタムルールのイベント生成 ===\n")
    
    today = timezone.now().date()
    total_created = 0
    
    for community_id, (name, rule_text) in CUSTOM_COMMUNITIES.items():
        try:
            community = Community.objects.get(id=community_id)
            
            # マスターイベントを取得
            master_event = community.events.filter(is_recurring_master=True).first()
            if not master_event:
                print(f"⚠️ {name}: マスターイベントが見つかりません")
                continue
            
            print(f"\n{name} (ID: {community_id}):")
            print(f"  ルール: {rule_text}")
            
            # 日付を生成
            dates = generate_custom_dates(rule_text, today, months=1)
            
            created_count = 0
            with transaction.atomic():
                for date in dates:
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
            
            print(f"  作成: {created_count}件")
            
            if created_count > 0:
                # 次回開催日を表示
                next_events = Event.objects.filter(
                    community=community,
                    date__gt=today
                ).order_by('date')[:5]
                
                dates_str = ', '.join(e.date.strftime('%Y-%m-%d') for e in next_events)
                print(f"  次回開催: {dates_str}")
            
            total_created += created_count
            
        except Community.DoesNotExist:
            print(f"⚠️ {name} (ID: {community_id}): コミュニティが見つかりません")
        except Exception as e:
            print(f"✗ {name} (ID: {community_id}): エラー - {e}")
    
    print(f"\n=== 生成完了 ===")
    print(f"作成したイベント: {total_created}件")

if __name__ == '__main__':
    generate_custom_events()