"""既存のGoogleカレンダーイベントを定期イベントに移行するコマンド"""
from datetime import datetime, timedelta
from collections import defaultdict
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from event.models import Event, RecurrenceRule
from event.google_calendar import GoogleCalendarService
from community.models import Community


class Command(BaseCommand):
    help = '既存のGoogleカレンダーイベントを定期イベントとして移行'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='実際には作成せず、移行予定の内容を表示'
        )
        parser.add_argument(
            '--community-id',
            type=int,
            help='特定のコミュニティのみ処理'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        community_id = options.get('community_id')
        
        # 処理対象のコミュニティを取得
        if community_id:
            communities = Community.objects.filter(id=community_id)
        else:
            communities = Community.objects.filter(status='approved')
        
        self.stdout.write('既存イベントの移行を開始します...')
        
        for community in communities:
            self.stdout.write(f'\n{community.name}を処理中...')
            
            # このコミュニティの既存イベントを取得
            existing_events = Event.objects.filter(
                community=community
            ).order_by('date', 'start_time')
            
            if not existing_events.exists():
                self.stdout.write('  移行対象のイベントがありません')
                continue
            
            self.stdout.write(f'  イベント数: {existing_events.count()}')
            
            # イベントをグループ化（開始時刻と曜日で分類）
            event_groups = defaultdict(list)
            for event in existing_events:
                key = (event.start_time, event.weekday, event.duration)
                event_groups[key].append(event)
            
            # 各グループを分析
            for (start_time, weekday, duration), events in event_groups.items():
                self.stdout.write(f'  グループ: {start_time} {weekday} ({duration}分) - {len(events)}件')
                if len(events) < 3:
                    # 3回未満は定期イベントとみなさない
                    self.stdout.write('    → 3回未満のためスキップ')
                    continue
                
                # 日付を並べて規則性を分析
                dates = sorted([e.date for e in events])
                pattern = self._analyze_pattern(dates)
                
                if pattern:
                    self.stdout.write(
                        f'  パターン検出: {start_time} {weekday} - {pattern["type"]} '
                        f'({len(events)}件のイベント)'
                    )
                    
                    if not dry_run:
                        self._create_recurring_events(
                            community, events, pattern, start_time, duration
                        )
                    else:
                        self.stdout.write(f'    → {pattern}')
                else:
                    # デバッグ情報を追加
                    intervals = []
                    for i in range(1, min(5, len(events))):
                        interval = (events[i].date - events[i-1].date).days
                        intervals.append(interval)
                    self.stdout.write(f'    → パターンが検出されませんでした (間隔: {intervals})')
        
        if dry_run:
            self.stdout.write(self.style.SUCCESS('\n[DRY RUN] 実際の移行は行われませんでした'))
        else:
            self.stdout.write(self.style.SUCCESS('\n移行が完了しました'))
    
    def _analyze_pattern(self, dates):
        """日付リストから定期パターンを分析"""
        if len(dates) < 3:
            return None
        
        # 連続する日付の間隔を計算
        intervals = []
        for i in range(1, len(dates)):
            interval = (dates[i] - dates[i-1]).days
            intervals.append(interval)
        
        # 間隔の最頻値を取得
        most_common_interval = max(set(intervals), key=intervals.count)
        interval_variance = sum(abs(i - most_common_interval) for i in intervals) / len(intervals)
        
        # 規則性の判定（平均誤差が3日以内）
        if interval_variance > 3:
            return None
        
        # パターンの種類を判定
        if 6 <= most_common_interval <= 8:
            return {
                'type': 'WEEKLY',
                'frequency': 'WEEKLY',
                'interval': 1
            }
        elif 13 <= most_common_interval <= 15:
            return {
                'type': 'BIWEEKLY',
                'frequency': 'WEEKLY',
                'interval': 2
            }
        elif 27 <= most_common_interval <= 31:
            # 月次かどうかをさらに分析
            days_of_month = [d.day for d in dates]
            if max(days_of_month) - min(days_of_month) <= 3:
                # 毎月同じ日付
                return {
                    'type': 'MONTHLY_BY_DATE',
                    'frequency': 'MONTHLY_BY_DATE',
                    'interval': 1
                }
            else:
                # 第N曜日の可能性をチェック
                weeks_of_month = []
                for date in dates:
                    first_day = date.replace(day=1)
                    week_of_month = ((date - first_day).days // 7) + 1
                    weeks_of_month.append(week_of_month)
                
                if len(set(weeks_of_month)) == 1:
                    return {
                        'type': 'MONTHLY_BY_WEEK',
                        'frequency': 'MONTHLY_BY_WEEK',
                        'interval': 1,
                        'week_of_month': weeks_of_month[0]
                    }
        
        return None
    
    def _create_recurring_events(self, community, events, pattern, start_time, duration):
        """定期イベントとして再作成"""
        with transaction.atomic():
            # RecurrenceRuleを作成
            rule = RecurrenceRule.objects.create(
                frequency=pattern['frequency'],
                interval=pattern.get('interval', 1),
                week_of_month=pattern.get('week_of_month'),
                custom_rule='',
                end_date=None
            )
            
            # 最初のイベントをマスターに
            first_event = events[0]
            first_event.recurrence_rule = rule
            first_event.is_recurring_master = True
            first_event.save()
            
            # 残りのイベントを子イベントに
            for event in events[1:]:
                event.recurring_master = first_event
                event.save()
            
            self.stdout.write(
                self.style.SUCCESS(
                    f'    → {len(events)}件のイベントを定期イベントとして移行しました'
                )
            )