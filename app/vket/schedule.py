"""Vket コラボの参加枠の重なり判定。

主催者の申込み検証・申込みフォームの空き表示・運営の日程画面の警告と表の色・
行の確定時の警告・公開同期前の検査は、すべてこのモジュールの関数で判定する。
枠は開始と終了を日付つきの日時で持ち、日付をまたぐ枠も比べる。
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import combinations

from django.db import transaction

from .models import VketCollaboration, VketParticipation

# 開催時間が未入力の参加を判定・表示するときの仮の長さ（日程表の既存表示と揃える）
DEFAULT_DURATION_MINUTES = 60
SCHEDULE_BUFFER_SETTING_KEY = 'schedule_buffer_minutes'
MAX_SCHEDULE_BUFFER_MINUTES = 120
# 公開同期で「重なりを承知で公開する」を選んだことを表す POST の項目
ALLOW_OVERLAP_FIELD = 'allow_overlap'

_BASE_DATE = date(2000, 1, 1)
_EPOCH = datetime(1970, 1, 1)
_ONE_MINUTE = timedelta(minutes=1)


@dataclass(frozen=True)
class ScheduleBlock:
    """1 つの参加が押さえている時間帯"""

    participation_id: int | None
    community_id: int
    community_name: str
    date: date
    start: time
    duration: int
    is_confirmed: bool = False

    @property
    def start_dt(self) -> datetime:
        return datetime.combine(self.date, self.start)

    @property
    def end_dt(self) -> datetime:
        return self.start_dt + timedelta(minutes=self.duration)

    @property
    def end(self) -> time:
        """終了時刻（日付をまたぐ時は翌日の時刻）"""
        return self.end_dt.time()

    def touched_dates(self) -> list[date]:
        """枠が一部でもかかる日付を返す"""
        last = (self.end_dt - _ONE_MINUTE).date() if self.duration > 0 else self.date
        days = (last - self.date).days
        return [self.date + timedelta(days=i) for i in range(days + 1)]


def get_schedule_buffer_minutes(collaboration: VketCollaboration) -> int:
    """コラボの入れ替えの間隔（分）を返す。設定が無い・不正な時は 0"""
    settings = collaboration.settings_json
    if not isinstance(settings, dict):
        return 0
    raw = settings.get(SCHEDULE_BUFFER_SETTING_KEY, 0)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 0
    return min(max(raw, 0), MAX_SCHEDULE_BUFFER_MINUTES)


def set_schedule_buffer_minutes(collaboration: VketCollaboration, minutes: int) -> None:
    """入れ替えの間隔だけを書き換える。

    行をロックして読み直し、他のキーへの同時更新を消さない。
    settings_json が dict でない時は dict として作り直す。
    """
    with transaction.atomic():
        locked = VketCollaboration.objects.select_for_update().get(pk=collaboration.pk)
        settings = locked.settings_json if isinstance(locked.settings_json, dict) else {}
        settings = {**settings, SCHEDULE_BUFFER_SETTING_KEY: minutes}
        locked.settings_json = settings
        locked.save(update_fields=['settings_json', 'updated_at'])
    collaboration.settings_json = settings


def ranges_conflict(
    start1: time,
    duration1: int,
    start2: time,
    duration2: int,
    buffer_minutes: int = 0,
) -> bool:
    """同じ日に始まる 2 つの時間帯が、前後に間隔を足したうえで重なるかを返す"""
    a = ScheduleBlock(None, 0, '', _BASE_DATE, start1, duration1)
    b = ScheduleBlock(None, 0, '', _BASE_DATE, start2, duration2)
    return blocks_conflict(a, b, buffer_minutes)


def blocks_conflict(a: ScheduleBlock, b: ScheduleBlock, buffer_minutes: int = 0) -> bool:
    """2 つの枠が、日付をまたぐ場合も含めて、間隔込みで重なるなら True"""
    gap = timedelta(minutes=buffer_minutes)
    return a.start_dt < b.end_dt + gap and b.start_dt < a.end_dt + gap


def block_for(participation: VketParticipation) -> ScheduleBlock | None:
    """参加の枠を返す。確定値（日付と開始時刻）があれば確定値、無ければ希望値"""
    is_confirmed = (
        participation.confirmed_date is not None
        and participation.confirmed_start_time is not None
    )
    if is_confirmed:
        d = participation.confirmed_date
        start = participation.confirmed_start_time
        duration = participation.confirmed_duration
    else:
        d = participation.requested_date
        start = participation.requested_start_time
        duration = participation.requested_duration
    if d is None or start is None:
        return None
    return ScheduleBlock(
        participation_id=participation.pk,
        community_id=participation.community_id,
        community_name=participation.community.name,
        date=d,
        start=start,
        duration=duration or DEFAULT_DURATION_MINUTES,
        is_confirmed=is_confirmed,
    )


def blocks_from(participations: Iterable[VketParticipation]) -> list[ScheduleBlock]:
    """有効な参加（取り消し・辞退を除く）の枠を、日時順に返す"""
    blocks = [
        block
        for p in participations
        if p.lifecycle == VketParticipation.Lifecycle.ACTIVE
        and (block := block_for(p)) is not None
    ]
    return sorted(blocks, key=lambda b: (b.start_dt, b.community_name))


def active_blocks(
    collaboration: VketCollaboration,
    *,
    exclude_community_id: int | None = None,
) -> list[ScheduleBlock]:
    """コラボ内の有効な参加の枠を DB から読んで返す"""
    qs = VketParticipation.objects.filter(
        collaboration=collaboration,
        lifecycle=VketParticipation.Lifecycle.ACTIVE,
    ).select_related('community')
    if exclude_community_id is not None:
        qs = qs.exclude(community_id=exclude_community_id)
    return blocks_from(qs)


def find_conflicts(
    collaboration: VketCollaboration,
    candidate: ScheduleBlock,
) -> list[ScheduleBlock]:
    """候補の枠と重なる、他の集会の有効な参加の枠を返す"""
    buffer_minutes = get_schedule_buffer_minutes(collaboration)
    return [
        block
        for block in active_blocks(collaboration, exclude_community_id=candidate.community_id)
        if blocks_conflict(candidate, block, buffer_minutes)
    ]


def find_conflicting_pairs(
    blocks: Iterable[ScheduleBlock],
    buffer_minutes: int = 0,
) -> list[tuple[ScheduleBlock, ScheduleBlock]]:
    """枠の一覧から、間隔を含めて重なっている組を返す"""
    ordered = sorted(blocks, key=lambda b: (b.start_dt, b.community_name))
    return [
        (a, b)
        for a, b in combinations(ordered, 2)
        if a.community_id != b.community_id and blocks_conflict(a, b, buffer_minutes)
    ]


def find_publish_target_conflicts(
    collaboration: VketCollaboration,
) -> list[tuple[ScheduleBlock, ScheduleBlock]]:
    """公開同期の対象（有効かつ確定日程が揃った参加）のうち、重なっている組を返す"""
    targets = collaboration.participations.filter(
        lifecycle=VketParticipation.Lifecycle.ACTIVE,
        confirmed_date__isnull=False,
        confirmed_start_time__isnull=False,
        confirmed_duration__isnull=False,
    ).select_related('community')
    return find_conflicting_pairs(blocks_from(targets), get_schedule_buffer_minutes(collaboration))


def format_block_range(block: ScheduleBlock) -> str:
    """枠の時間帯を「21:00〜22:00」「23:30〜翌01:00」の形にする"""
    next_day = '翌' if block.end_dt.date() > block.date else ''
    return f'{block.start:%H:%M}〜{next_day}{block.end:%H:%M}'


def format_pair(a: ScheduleBlock, b: ScheduleBlock) -> str:
    """重なっている組を運営向けの 1 行にする"""
    return (
        f'{a.date.strftime("%Y/%m/%d")} '
        f'{a.community_name}（{format_block_range(a)}）と '
        f'{b.community_name}（{format_block_range(b)}）'
    )


def _day_label(block: ScheduleBlock, day: date) -> str:
    """その日から見た枠の時間帯の表示"""
    if block.date < day:
        return f'〜{block.end:%H:%M}（前日から）'
    return format_block_range(block)


def _minutes_since_epoch(value: datetime) -> int:
    return int((value - _EPOCH).total_seconds() // 60)


def busy_payload(blocks: Iterable[ScheduleBlock]) -> dict:
    """申込みフォームの空き表示用のデータを返す。

    blocks は判定用の枠の一覧（1970-01-01 からの分で開始・終了を持つ）、
    days は日付ごとに、その日に一部でもかかる枠の番号と表示を持つ。
    集会名と時間帯だけを含め、それ以外の参加情報は出さない。
    """
    items: list[dict] = []
    days: dict[str, list[dict]] = {}
    for index, block in enumerate(blocks):
        items.append({
            'name': block.community_name,
            'start_abs': _minutes_since_epoch(block.start_dt),
            'end_abs': _minutes_since_epoch(block.end_dt),
        })
        for day in block.touched_dates():
            days.setdefault(day.isoformat(), []).append({
                'index': index,
                'label': _day_label(block, day),
            })
    return {'blocks': items, 'days': days}
