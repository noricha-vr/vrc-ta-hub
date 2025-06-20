#!/usr/bin/env python
"""すべてのイベントを再作成してGoogleカレンダーと同期"""

import os
import sys
import django
from datetime import datetime, date, timedelta
import json

# Django設定
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
django.setup()

from django.utils import timezone
from event.models import RecurrenceRule, Event
from community.models import Community
from event.sync_to_google import DatabaseToGoogleSync

def backup_current_events():
    """現在のイベントをバックアップ"""
    print("=== 既存イベントのバックアップ ===")
    
    today = timezone.now().date()
    future_events = Event.objects.filter(date__gte=today).select_related('community', 'recurrence_rule')
    
    backup_data = []
    for event in future_events:
        backup_data.append({
            'id': event.id,
            'community': event.community.name,
            'date': str(event.date),
            'start_time': str(event.start_time),
            'duration': event.duration,
            'google_calendar_event_id': event.google_calendar_event_id,
            'is_recurring_master': event.is_recurring_master,
            'recurrence_rule_id': event.recurrence_rule_id if event.recurrence_rule else None
        })
    
    # バックアップファイルに保存
    backup_file = f'/opt/project/scripts/event_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    with open(backup_file, 'w') as f:
        json.dump(backup_data, f, indent=2, ensure_ascii=False)
    
    print(f"✓ {len(backup_data)}件のイベントをバックアップしました: {backup_file}")
    return len(backup_data)

def clear_google_calendar():
    """Googleカレンダーの今日以降のイベントを削除"""
    print("\n=== Googleカレンダーのクリア ===")
    
    sync = DatabaseToGoogleSync()
    today = timezone.now().date()
    end_date = today + timedelta(days=365)  # 1年先まで
    
    # すべてのコミュニティのGoogleカレンダーイベントを取得
    all_events = sync.service.list_events(
        time_min=datetime.combine(today, datetime.min.time()),
        time_max=datetime.combine(end_date, datetime.max.time())
    )
    
    deleted_count = 0
    for event in all_events:
        try:
            sync.service.delete_event(event['id'])
            deleted_count += 1
        except Exception as e:
            print(f"✗ イベント削除エラー: {e}")
    
    print(f"✓ Googleカレンダーから{deleted_count}件のイベントを削除しました")
    return deleted_count

def clear_database_events():
    """データベースの今日以降のイベントを削除"""
    print("\n=== データベースイベントのクリア ===")
    
    today = timezone.now().date()
    future_events = Event.objects.filter(date__gte=today)
    event_count = future_events.count()
    
    # google_calendar_event_idをクリア
    future_events.update(google_calendar_event_id=None)
    
    # イベントを削除
    future_events.delete()
    
    print(f"✓ データベースから{event_count}件のイベントを削除しました")
    return event_count

def get_week_of_month(date_obj):
    """日付から第N週を計算"""
    first_day = date_obj.replace(day=1)
    first_weekday = first_day.weekday()
    
    # 第1週の日数を計算
    days_in_first_week = 7 - first_weekday
    
    if date_obj.day <= days_in_first_week:
        return 1
    else:
        return ((date_obj.day - days_in_first_week - 1) // 7) + 2

def is_week_match(date_obj, custom_rule):
    """日付が隔週パターンに一致するか確認"""
    if not custom_rule:
        return True
    
    # 週番号を計算（1月1日を第1週として）
    year_start = date(date_obj.year, 1, 1)
    days_diff = (date_obj - year_start).days
    week_num = (days_diff // 7) + 1
    
    if custom_rule == 'biweekly_A':
        return week_num % 2 == 1  # 奇数週
    elif custom_rule == 'biweekly_B':
        return week_num % 2 == 0  # 偶数週
    
    return True

def create_events_from_rules():
    """RecurrenceRuleに基づいて新規イベントを生成"""
    print("\n=== 新規イベントの生成 ===")
    
    communities = Community.objects.filter(status='approved')
    today = timezone.now().date()
    end_date = today + timedelta(days=90)  # 3ヶ月先まで
    
    created_count = 0
    
    for community in communities:
        # RecurrenceRuleを持つイベントを探す
        master_event = community.events.filter(is_recurring_master=True).first()
        
        if master_event and master_event.recurrence_rule:
            rule = master_event.recurrence_rule
            
            # 開始日を決定
            if community.weekdays:
                weekdays = eval(community.weekdays) if isinstance(community.weekdays, str) else community.weekdays
                weekday_map = {'Mon': 0, 'Tue': 1, 'Wed': 2, 'Thu': 3, 'Fri': 4, 'Sat': 5, 'Sun': 6}
                target_weekday = weekday_map.get(weekdays[0], 0) if weekdays else 0
            else:
                target_weekday = 0
            
            current_date = today
            # 最初の対象曜日まで進める
            while current_date.weekday() != target_weekday:
                current_date += timedelta(days=1)
            
            # イベントを生成
            while current_date <= end_date:
                should_create = False
                
                if rule.frequency == 'WEEKLY':
                    if rule.interval == 1:
                        should_create = True
                    elif rule.interval == 2:
                        should_create = is_week_match(current_date, rule.custom_rule)
                
                elif rule.frequency == 'MONTHLY_BY_WEEK':
                    week_of_month = get_week_of_month(current_date)
                    if rule.week_of_month:
                        should_create = (week_of_month == rule.week_of_month)
                    else:
                        # 第N週の指定がない場合は月1回
                        if current_date.day <= 7:
                            should_create = True
                
                elif rule.frequency == 'MONTHLY_BY_DATE':
                    # 毎月同じ日付
                    if master_event.date.day == current_date.day:
                        should_create = True
                
                if should_create:
                    # 既存のイベントがないか確認
                    existing = Event.objects.filter(
                        community=community,
                        date=current_date
                    ).exists()
                    
                    if not existing:
                        Event.objects.create(
                            community=community,
                            date=current_date,
                            start_time=community.start_time,
                            duration=community.duration,
                            is_recurring_master=False,
                            recurrence_rule=None  # 個別イベントにはRecurrenceRuleを設定しない
                        )
                        created_count += 1
                
                # 次の週へ
                if rule.frequency == 'WEEKLY':
                    current_date += timedelta(days=7 * rule.interval)
                elif rule.frequency.startswith('MONTHLY'):
                    # 翌月の同じ曜日へ
                    next_month = current_date.month + 1
                    next_year = current_date.year
                    if next_month > 12:
                        next_month = 1
                        next_year += 1
                    
                    try:
                        current_date = current_date.replace(year=next_year, month=next_month)
                    except ValueError:
                        # 月末の場合の処理
                        current_date = current_date.replace(year=next_year, month=next_month, day=1)
                        current_date = current_date.replace(day=28)
                else:
                    break
    
    print(f"✓ {created_count}件の新規イベントを生成しました")
    return created_count

def sync_to_google_calendar():
    """生成したイベントをGoogleカレンダーに同期"""
    print("\n=== Googleカレンダーへの同期 ===")
    
    sync = DatabaseToGoogleSync()
    stats = sync.sync_all_communities(months_ahead=3)
    
    total_created = sum(s.get('created', 0) for s in stats if isinstance(s, dict))
    total_errors = sum(s.get('errors', 0) for s in stats if isinstance(s, dict))
    
    print(f"✓ {total_created}件のイベントをGoogleカレンダーに作成しました")
    if total_errors > 0:
        print(f"⚠️ {total_errors}件のエラーが発生しました")
    
    return total_created

def check_duplicates():
    """重複チェック"""
    print("\n=== 重複チェック ===")
    
    # データベース内の重複チェック
    from django.db.models import Count
    
    duplicates = Event.objects.filter(
        date__gte=timezone.now().date()
    ).values('community', 'date', 'start_time').annotate(
        count=Count('id')
    ).filter(count__gt=1)
    
    if duplicates:
        print(f"⚠️ データベースに{len(duplicates)}件の重複が見つかりました:")
        for dup in duplicates[:10]:  # 最初の10件を表示
            community = Community.objects.get(id=dup['community'])
            print(f"  - {community.name}: {dup['date']} {dup['start_time']} ({dup['count']}件)")
    else:
        print("✓ データベースに重複はありません")
    
    # Googleカレンダーの重複チェック
    sync = DatabaseToGoogleSync()
    today = timezone.now().date()
    end_date = today + timedelta(days=90)
    
    all_events = sync.service.list_events(
        time_min=datetime.combine(today, datetime.min.time()),
        time_max=datetime.combine(end_date, datetime.max.time())
    )
    
    # イベントをグループ化
    google_events = {}
    for event in all_events:
        key = (event.get('summary', ''), event.get('start', {}).get('dateTime', ''))
        if key in google_events:
            google_events[key].append(event)
        else:
            google_events[key] = [event]
    
    google_duplicates = [(k, v) for k, v in google_events.items() if len(v) > 1]
    
    if google_duplicates:
        print(f"\n⚠️ Googleカレンダーに{len(google_duplicates)}件の重複が見つかりました:")
        for (summary, start_time), events in google_duplicates[:10]:
            print(f"  - {summary}: {start_time} ({len(events)}件)")
    else:
        print("\n✓ Googleカレンダーに重複はありません")
    
    return len(duplicates), len(google_duplicates)

def main():
    """メイン処理"""
    print("=== イベント再作成処理を開始します ===\n")
    
    # 1. バックアップ
    backup_count = backup_current_events()
    
    # 2. Googleカレンダーをクリア
    google_deleted = clear_google_calendar()
    
    # 3. データベースをクリア
    db_deleted = clear_database_events()
    
    # 4. 新規イベントを生成
    created_count = create_events_from_rules()
    
    # 5. Googleカレンダーに同期
    synced_count = sync_to_google_calendar()
    
    # 6. 重複チェック
    db_dup, google_dup = check_duplicates()
    
    # サマリー
    print("\n=== 処理完了サマリー ===")
    print(f"バックアップ: {backup_count}件")
    print(f"削除: Google {google_deleted}件, DB {db_deleted}件")
    print(f"生成: {created_count}件")
    print(f"同期: {synced_count}件")
    print(f"重複: DB {db_dup}件, Google {google_dup}件")
    
    if db_dup == 0 and google_dup == 0:
        print("\n✅ すべての処理が正常に完了しました！")
    else:
        print("\n⚠️ 重複が検出されました。確認が必要です。")

if __name__ == '__main__':
    main()