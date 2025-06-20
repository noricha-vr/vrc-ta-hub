"""既存イベントの定期パターンを分析するコマンド（マイグレーション不要）"""
from datetime import datetime, timedelta
from collections import defaultdict
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from event.models import Event
from community.models import Community


class Command(BaseCommand):
    help = '既存イベントの定期パターンを分析'

    def add_arguments(self, parser):
        parser.add_argument(
            '--community-id',
            type=int,
            help='特定のコミュニティのみ処理'
        )

    def handle(self, *args, **options):
        community_id = options.get('community_id')
        
        # 処理対象のコミュニティを取得
        if community_id:
            communities = Community.objects.filter(id=community_id)
        else:
            communities = Community.objects.filter(status='approved')
        
        self.stdout.write('既存イベントの定期パターンを分析します...')
        
        total_patterns = 0
        total_events = 0
        
        for community in communities:
            # このコミュニティの既存イベントを取得
            existing_events = Event.objects.filter(
                community=community
            ).order_by('date', 'start_time')
            
            if existing_events.count() < 3:
                continue
            
            # イベントをグループ化（開始時刻と曜日で分類）
            event_groups = defaultdict(list)
            for event in existing_events:
                key = (event.start_time, event.weekday, event.duration)
                event_groups[key].append(event)
            
            # 各グループを分析
            community_has_pattern = False
            for (start_time, weekday, duration), events in event_groups.items():
                if len(events) < 3:
                    # 3回未満は定期イベントとみなさない
                    continue
                
                # 日付を並べて規則性を分析
                dates = sorted([e.date for e in events])
                pattern = self._analyze_pattern(dates)
                
                if pattern:
                    if not community_has_pattern:
                        self.stdout.write(f'\n{community.name}:')
                        community_has_pattern = True
                    
                    self.stdout.write(
                        f'  パターン検出: {start_time} {weekday} - {pattern["type"]} '
                        f'({len(events)}件のイベント)'
                    )
                    
                    # 最初の5つの日付を表示
                    self.stdout.write('    日付:')
                    for i, date in enumerate(dates[:5]):
                        self.stdout.write(f'      - {date}')
                    if len(dates) > 5:
                        self.stdout.write(f'      ... 他{len(dates) - 5}件')
                    
                    total_patterns += 1
                    total_events += len(events)
        
        self.stdout.write(
            self.style.SUCCESS(
                f'\n\n分析完了: {total_patterns}個の定期パターン、'
                f'合計{total_events}件のイベントが移行対象です'
            )
        )
    
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
        
        # 規則性の判定（平均誤差が2日以内）
        if interval_variance > 2:
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