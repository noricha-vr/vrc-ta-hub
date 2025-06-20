"""定期イベントのインスタンスを生成するコマンド"""
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from event.models import Event, RecurrenceRule
from event.recurrence_service import RecurrenceService


class Command(BaseCommand):
    help = '定期イベントのインスタンスを生成（3ヶ月先まで）'

    def add_arguments(self, parser):
        parser.add_argument(
            '--months',
            type=int,
            default=3,
            help='何ヶ月先まで生成するか（デフォルト: 3）'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='実際には作成せず、作成予定のイベントを表示'
        )

    def handle(self, *args, **options):
        months = options['months']
        dry_run = options['dry_run']
        
        self.stdout.write(f'{months}ヶ月先までの定期イベントを生成します...')
        
        # アクティブな定期ルールを持つマスターイベントを取得
        master_events = Event.objects.filter(
            is_recurring_master=True,
            recurrence_rule__isnull=False
        ).select_related('recurrence_rule', 'community')
        
        total_created = 0
        service = RecurrenceService()
        
        for master in master_events:
            rule = master.recurrence_rule
            community = master.community
            
            # 最後に生成されたイベントの日付を取得
            last_instance = Event.objects.filter(
                recurring_master=master
            ).order_by('-date').first()
            
            # 基準日を決定（最後のインスタンスの次の日、またはマスターイベントの日付）
            if last_instance:
                base_date = last_instance.date + timedelta(days=1)
            else:
                base_date = master.date
            
            # 今日より前の日付なら今日に設定
            today = timezone.now().date()
            if base_date < today:
                base_date = today
            
            # 終了日を計算
            end_date = today + timedelta(days=months * 30)
            if rule.end_date and rule.end_date < end_date:
                end_date = rule.end_date
            
            # 生成期間が有効か確認
            if base_date > end_date:
                self.stdout.write(
                    self.style.WARNING(
                        f'{community.name} - {master.date}: 生成期間外のためスキップ'
                    )
                )
                continue
            
            # 日付リストを生成
            dates = service.generate_dates(
                rule=rule,
                base_date=base_date,
                base_time=master.start_time,
                months=months
            )
            
            # 既存のイベントを除外
            new_dates = []
            for date in dates:
                if date >= base_date and date <= end_date:
                    exists = Event.objects.filter(
                        community=community,
                        date=date,
                        start_time=master.start_time
                    ).exists()
                    if not exists:
                        new_dates.append(date)
            
            if dry_run:
                self.stdout.write(
                    f'\n{community.name} - {master.date} ({rule}):'
                )
                for date in new_dates:
                    self.stdout.write(f'  - {date}')
            else:
                # イベントを作成
                created_count = 0
                with transaction.atomic():
                    for date in new_dates:
                        Event.objects.create(
                            community=community,
                            date=date,
                            start_time=master.start_time,
                            duration=master.duration,
                            weekday=date.strftime('%a').upper()[:3],
                            recurring_master=master
                        )
                        created_count += 1
                
                if created_count > 0:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'{community.name}: {created_count}件のイベントを作成'
                        )
                    )
                
                total_created += created_count
        
        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f'\n合計 {sum(len(dates) for dates in [new_dates])}件のイベントが作成される予定です'
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'\n合計 {total_created}件のイベントを作成しました'
                )
            )