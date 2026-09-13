"""開催予定フォームから単発・定期イベントを一括登録する。"""

from django.core.exceptions import ValidationError
from django.db import transaction

from community.constants import WEEKDAY_CHOICES, weekday_code
from community.forms_processor import refresh_calendar_entry_and_event_cache
from community.models import Community
from event.models import Event, RecurrenceRule
from event.recurrence import RecurrenceService


def register_calendar_events(community, data):
    """集会単位で直列化し、開催予定と掲載用の開催情報を同時に保存する。"""
    with transaction.atomic():
        community = Community.objects.select_for_update().get(pk=community.pk)
        if community.end_at is not None or community.status != 'approved':
            raise ValidationError('開催予定の登録には、承認済みの集会を再開してください。')
        start_date = data['start_date']
        start_time = data['start_time']
        duration = data['duration']
        recurrence_type = data['recurrence_type']
        code = weekday_code(start_date)
        weekdays = [code]
        if recurrence_type == 'none':
            # unique 制約で競合を判定。呼び出し元で IntegrityError を処理する。
            events = [Event.objects.create(
                community=community, date=start_date, start_time=start_time,
                duration=duration, weekday=code,
            )]
            frequency = '単発'
        else:
            # 生成サービスは既存日を飛ばすため、初回の衝突を先に弾く。
            if Event.objects.filter(community=community, date=start_date).exists():
                raise ValidationError('初回開催日にはすでにイベントが登録されています。')
            frequency_type, interval = {
                'weekly': ('WEEKLY', 1),
                'biweekly': ('WEEKLY', 2),
                'monthly_by_date': ('MONTHLY_BY_DATE', 1),
                'monthly_by_day': ('MONTHLY_BY_WEEK', 1),
            }[recurrence_type]
            week = int(data['week_number']) if recurrence_type == 'monthly_by_day' else None
            rule = RecurrenceRule.objects.create(
                community=community, frequency=frequency_type, interval=interval,
                start_date=start_date, week_of_month=week,
            )
            events = RecurrenceService().create_recurring_events(
                community, rule, start_date, start_time, duration, months=3,
            )
            if not events or events[0].date != start_date:
                raise ValidationError(
                    '初回開催日を登録できませんでした。削除済みの日付を避けて登録してください。')
            rule.last_generated_date = max(event.date for event in events)
            rule.save(update_fields=['last_generated_date', 'updated_at'])
            if recurrence_type == 'monthly_by_date':
                frequency = f'毎月{start_date.day}日'
                weekdays = []
            elif recurrence_type == 'monthly_by_day':
                ordinal = '最終' if week == -1 else f'第{week}'
                frequency = f'毎月{ordinal}{dict(WEEKDAY_CHOICES)[code]}曜日'
            else:
                frequency = '毎週' if interval == 1 else '隔週'

        community.frequency = frequency
        community.weekdays = weekdays
        community.start_time = start_time
        community.duration = duration
        community.save(update_fields=['frequency', 'weekdays', 'start_time', 'duration', 'updated_at'])
        refresh_calendar_entry_and_event_cache(community)
        return events
