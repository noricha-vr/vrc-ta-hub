"""旧 Community.frequency を定期開催ルールへ移行する."""
import re
from calendar import monthrange
from datetime import date, datetime, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from community.models import Community
from event.models import Event, RecurrenceRule

WEEKDAY_CODE_TO_INDEX = {
    'Mon': 0,
    'Tue': 1,
    'Wed': 2,
    'Thu': 3,
    'Fri': 4,
    'Sat': 5,
    'Sun': 6,
}
JAPANESE_WEEKDAY_TO_INDEX = {
    '月': 0,
    '火': 1,
    '水': 2,
    '木': 3,
    '金': 4,
    '土': 5,
    '日': 6,
}
INDEX_TO_WEEKDAY_CODE = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')


class Command(BaseCommand):
    help = '判定可能な旧 Community.frequency を RecurrenceRule と定期親イベントへ移行'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='作成せずに移行予定を表示')
        parser.add_argument('--community-id', type=int, help='特定の集会のみ処理')
        parser.add_argument(
            '--base-date',
            type=str,
            help='親イベントの基準日（YYYY-MM-DD）。未指定なら今日',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        base_date = self._parse_base_date(options.get('base_date'))
        communities = Community.objects.filter(status='approved')
        if options.get('community_id'):
            communities = communities.filter(pk=options['community_id'])

        migrated_count = 0
        skipped_count = 0
        for community in communities.order_by('pk'):
            if community.recurrence_rules.exists():
                skipped_count += 1
                self.stdout.write(f'{community.name}: 既存の定期ルールがあるためスキップ')
                continue

            pattern = self._parse_frequency(community.frequency, community, base_date)
            if pattern is None:
                skipped_count += 1
                self.stdout.write(f'{community.name}: 判定不可 ({community.frequency})')
                continue

            self.stdout.write(
                f'{community.name}: {community.frequency} -> {pattern["frequency"]} '
                f'{pattern["start_date"]} interval={pattern["interval"]}'
            )
            if dry_run:
                migrated_count += 1
                continue

            with transaction.atomic():
                rule = RecurrenceRule.objects.create(
                    community=community,
                    frequency=pattern['frequency'],
                    interval=pattern['interval'],
                    week_of_month=pattern.get('week_of_month'),
                    custom_rule=pattern.get('custom_rule', ''),
                    start_date=pattern['start_date'],
                )
                event, _ = Event.objects.get_or_create(
                    community=community,
                    date=pattern['start_date'],
                    start_time=community.start_time,
                    defaults={
                        'duration': community.duration,
                        'weekday': INDEX_TO_WEEKDAY_CODE[pattern['start_date'].weekday()],
                    },
                )
                event.duration = community.duration
                event.weekday = INDEX_TO_WEEKDAY_CODE[pattern['start_date'].weekday()]
                event.recurrence_rule = rule
                event.is_recurring_master = True
                event.recurring_master = None
                event.save()
                migrated_count += 1

        status_label = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(
            self.style.SUCCESS(f'{status_label}移行候補 {migrated_count}件 / スキップ {skipped_count}件')
        )

    def _parse_base_date(self, value: str | None) -> date:
        if not value:
            return timezone.localdate()
        return datetime.strptime(value, '%Y-%m-%d').date()

    def _parse_frequency(self, frequency: str, community: Community, base_date: date) -> dict | None:
        normalized = self._normalize(frequency)
        weekday = self._get_primary_weekday(community)

        if normalized in {'毎週', 'weekly', 'everyweek'} and weekday is not None:
            return {
                'frequency': 'WEEKLY',
                'interval': 1,
                'start_date': self._next_weekday(base_date, weekday),
            }
        if normalized in {'隔週', 'biweekly', 'every2weeks'} and weekday is not None:
            return {
                'frequency': 'WEEKLY',
                'interval': 2,
                'start_date': self._next_weekday(base_date, weekday),
            }

        monthly_date = re.fullmatch(r'毎月(\d{1,2})日(?:開催)?', normalized)
        if monthly_date:
            day = int(monthly_date.group(1))
            if 1 <= day <= 31:
                return {
                    'frequency': 'OTHER',
                    'interval': 1,
                    'custom_rule': f'毎月{day}日',
                    'start_date': self._next_monthly_date(base_date, day),
                }

        monthly_week = re.fullmatch(r'毎月第([1-5])([月火水木金土日])曜(?:日)?', normalized)
        if monthly_week:
            week_of_month = int(monthly_week.group(1))
            weekday = JAPANESE_WEEKDAY_TO_INDEX[monthly_week.group(2)]
            return {
                'frequency': 'MONTHLY_BY_WEEK',
                'interval': 1,
                'week_of_month': week_of_month,
                'start_date': self._next_monthly_weekday(base_date, week_of_month, weekday),
            }

        return None

    def _normalize(self, value: str) -> str:
        normalized = (value or '').strip().lower()
        normalized = normalized.translate(str.maketrans('０１２３４５６７８９', '0123456789'))
        return re.sub(r'\s+', '', normalized)

    def _get_primary_weekday(self, community: Community) -> int | None:
        for weekday_code in community.weekdays:
            if weekday_code in WEEKDAY_CODE_TO_INDEX:
                return WEEKDAY_CODE_TO_INDEX[weekday_code]
        return None

    def _next_weekday(self, base_date: date, weekday: int) -> date:
        days_ahead = (weekday - base_date.weekday()) % 7
        return base_date + timedelta(days=days_ahead)

    def _next_monthly_date(self, base_date: date, day: int) -> date:
        year = base_date.year
        month = base_date.month
        candidate = self._monthly_date(year, month, day)
        if candidate < base_date:
            month += 1
            if month > 12:
                month = 1
                year += 1
            candidate = self._monthly_date(year, month, day)
        return candidate

    def _monthly_date(self, year: int, month: int, day: int) -> date:
        return date(year, month, min(day, monthrange(year, month)[1]))

    def _next_monthly_weekday(self, base_date: date, week_of_month: int, weekday: int) -> date:
        year = base_date.year
        month = base_date.month
        candidate = self._monthly_weekday(year, month, week_of_month, weekday)
        if candidate < base_date:
            month += 1
            if month > 12:
                month = 1
                year += 1
            candidate = self._monthly_weekday(year, month, week_of_month, weekday)
        return candidate

    def _monthly_weekday(self, year: int, month: int, week_of_month: int, weekday: int) -> date:
        month_start = date(year, month, 1)
        days_ahead = (weekday - month_start.weekday()) % 7
        return month_start + timedelta(days=days_ahead + (week_of_month - 1) * 7)
