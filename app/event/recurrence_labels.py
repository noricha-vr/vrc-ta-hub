"""定期開催ルールの公開表示ラベルを生成する."""
import re
from dataclasses import dataclass
from datetime import time

from community.models import WEEKDAY_CHOICES, Community
from event.models import Event, RecurrenceRule

UNSET_RECURRENCE_LABEL = '定期開催未設定'
WEEKDAY_LABELS = dict(WEEKDAY_CHOICES)
PYTHON_WEEKDAY_TO_CODE = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')


@dataclass(frozen=True)
class RecurrenceSchedule:
    rule: RecurrenceRule
    start_time: time
    duration: int


def get_community_recurrence_label(community: Community) -> str:
    """集会の定期開催ルールから公開向けの開催日程文を返す."""
    schedule = _get_primary_recurrence_schedule(community)
    if schedule is None:
        return UNSET_RECURRENCE_LABEL
    return format_recurrence_schedule(schedule.rule, schedule.start_time, schedule.duration)


def format_recurrence_schedule(rule: RecurrenceRule, start_time: time, duration: int) -> str:
    """定期ルールを内部分類名なしの開催日程文に整形する."""
    time_label = _format_time_range(start_time, duration)

    if rule.frequency == 'WEEKLY':
        weekday = _get_rule_weekday_label(rule)
        if rule.interval == 2:
            return f'隔週{weekday} {time_label}'
        if rule.interval and rule.interval > 2:
            return f'{rule.interval}週ごと{weekday} {time_label}'
        return f'毎週{weekday} {time_label}'

    if rule.frequency == 'MONTHLY_BY_WEEK':
        weekday = _get_rule_weekday_label(rule)
        week_label = _format_week_of_month(rule.week_of_month)
        return f'毎月{week_label}{weekday} {time_label}'

    if rule.frequency == 'MONTHLY_BY_DATE':
        day = rule.start_date.day if rule.start_date else None
        day_label = f'{day}日' if day else '日付指定'
        return f'毎月{day_label} {time_label}'

    custom_rule = (rule.custom_rule or '').strip()
    if rule.frequency == 'OTHER' and custom_rule:
        if _is_simple_monthly_date_rule(custom_rule):
            return f'{custom_rule} {time_label}'
        return custom_rule

    return UNSET_RECURRENCE_LABEL


def _get_primary_recurrence_schedule(community: Community) -> RecurrenceSchedule | None:
    master = (
        Event.objects.filter(
            community=community,
            is_recurring_master=True,
            recurrence_rule__isnull=False,
        )
        .select_related('recurrence_rule')
        .order_by('-date', '-id')
        .first()
    )
    if master:
        return RecurrenceSchedule(master.recurrence_rule, master.start_time, master.duration)

    rule = community.recurrence_rules.order_by('-updated_at', '-id').first()
    if rule is None:
        return None
    return RecurrenceSchedule(rule, community.start_time, community.duration)


def _format_time_range(start_time: time, duration: int) -> str:
    start_minutes = start_time.hour * 60 + start_time.minute
    end_minutes = start_minutes + duration
    end_hour = (end_minutes // 60) % 24
    end_minute = end_minutes % 60
    return f'{start_time:%H:%M}-{end_hour:02d}:{end_minute:02d}'


def _get_rule_weekday_label(rule: RecurrenceRule) -> str:
    if rule.start_date:
        weekday_code = PYTHON_WEEKDAY_TO_CODE[rule.start_date.weekday()]
        return WEEKDAY_LABELS.get(weekday_code, '')
    if rule.community and rule.community.weekdays:
        return WEEKDAY_LABELS.get(rule.community.weekdays[0], '')
    return ''


def _format_week_of_month(week_of_month: int | None) -> str:
    if week_of_month == -1:
        return '最終'
    if week_of_month:
        return f'第{week_of_month}'
    return ''


def _is_simple_monthly_date_rule(custom_rule: str) -> bool:
    normalized_rule = custom_rule.translate(str.maketrans('０１２３４５６７８９', '0123456789'))
    return re.fullmatch(r'毎月\d{1,2}日(?:開催)?', normalized_rule.strip()) is not None
