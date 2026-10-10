"""Vket コラボの発表時間の重なり判定。

主催者の保存後の警告・申込みフォームの空き表示・運営の日程画面の警告と表の色・
行の確定時の警告・公開同期後の警告は、すべてこのモジュールの関数で判定する。
枠は開始と終了を日付つきの日時で持ち、日付をまたぐ枠も比べる。
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from itertools import combinations

from django.db import transaction

from .models import VketCollaboration, VketParticipation, VketPresentation

# 開催時間が未入力の参加を判定・表示するときの仮の長さ（日程表の既存表示と揃える）
DEFAULT_DURATION_MINUTES = 60
SCHEDULE_BUFFER_SETTING_KEY = 'schedule_buffer_minutes'
MAX_SCHEDULE_BUFFER_MINUTES = 120
_EPOCH = datetime(1970, 1, 1)
_ONE_MINUTE = timedelta(minutes=1)


@dataclass(frozen=True)
class ScheduleBlock:
    """発表の判定用・参加の日程表示用の時間帯"""

    participation_id: int | None
    community_id: int
    community_name: str
    date: date
    start: time
    duration: int
    is_confirmed: bool = False
    presentation_id: int | None = None

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


def blocks_conflict(a: ScheduleBlock, b: ScheduleBlock, buffer_minutes: int = 0) -> bool:
    """2 つの枠が、日付をまたぐ場合も含めて、間隔込みで重なるなら True"""
    gap = timedelta(minutes=buffer_minutes)
    return a.start_dt < b.end_dt + gap and b.start_dt < a.end_dt + gap


def block_for(participation: VketParticipation) -> ScheduleBlock | None:
    """日程表の参加枠を返す。日付・時刻・長さはそれぞれ確定値を優先する"""
    is_confirmed = (
        participation.confirmed_date is not None
        and participation.confirmed_start_time is not None
    )
    d = participation.effective_date
    start = participation.effective_start_time
    duration = participation.effective_duration
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


def presentation_block_for(
    participation: VketParticipation, presentation: VketPresentation,
    *, use_requested: bool = False,
) -> ScheduleBlock | None:
    """発表の時間を返す。希望の判定時以外は日付・開始時刻の確定値を優先する。"""
    if participation.lifecycle != VketParticipation.Lifecycle.ACTIVE:
        return None
    d = participation.requested_date if use_requested else participation.confirmed_date or participation.requested_date
    start = presentation.requested_start_time if use_requested else presentation.confirmed_start_time or presentation.requested_start_time
    if d is None or start is None or not presentation.duration:
        return None
    return ScheduleBlock(
        participation_id=participation.pk,
        community_id=participation.community_id,
        community_name=participation.community.name,
        date=d,
        start=start,
        duration=presentation.duration,
        is_confirmed=presentation.status == VketPresentation.Status.CONFIRMED,
        presentation_id=presentation.pk,
    )


def blocks_from(participations: Iterable[VketParticipation], *, use_requested: bool = False) -> list[ScheduleBlock]:
    """有効な参加の発表（取り下げた行・開始時刻なしを除く）を日時順に返す"""
    blocks = [
        block
        for p in participations
        for presentation in p.presentations.all()
        if (block := presentation_block_for(p, presentation, use_requested=use_requested)) is not None
    ]
    return sorted(blocks, key=lambda b: (b.start_dt, b.community_name, b.presentation_id or 0))


def active_blocks(
    collaboration: VketCollaboration,
    *,
    exclude_community_id: int | None = None,
) -> list[ScheduleBlock]:
    """コラボ内の有効な参加の発表時間を DB から読んで返す"""
    qs = VketParticipation.objects.filter(
        collaboration=collaboration,
        lifecycle=VketParticipation.Lifecycle.ACTIVE,
    ).select_related('community').prefetch_related('presentations')
    if exclude_community_id is not None:
        qs = qs.exclude(community_id=exclude_community_id)
    return blocks_from(qs)


def find_conflicts(
    collaboration: VketCollaboration,
    candidate: ScheduleBlock,
) -> list[ScheduleBlock]:
    """候補の枠と重なる、他の集会の有効な参加の発表時間を返す"""
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


def is_fully_confirmed(participation: VketParticipation) -> bool:
    """確定日程（日付・開始時刻・開催時間）が揃っている参加なら True"""
    return (
        participation.confirmed_date is not None
        and participation.confirmed_start_time is not None
        and participation.confirmed_duration is not None
    )


def confirmed_conflicting_pairs(
    collaboration: VketCollaboration,
    participations: Iterable[VketParticipation],
) -> list[tuple[ScheduleBlock, ScheduleBlock]]:
    """読み込み済みの参加のうち、確定済みの参加の発表どうしで重なっている組を返す（公開同期の対象）"""
    targets = [p for p in participations if is_fully_confirmed(p)]
    return find_conflicting_pairs(blocks_from(targets), get_schedule_buffer_minutes(collaboration))


def format_block_range(block: ScheduleBlock) -> str:
    """枠の時間帯を「21:00〜22:00」「23:30〜翌01:00」の形にする"""
    next_day = '翌' if block.end_dt.date() > block.date else ''
    return f'{block.start:%H:%M}〜{next_day}{block.end:%H:%M}'


def _date_prefix(block: ScheduleBlock, base: date) -> str:
    """基準の日から見た相手の日付の書き方（同じ日なら無し）"""
    if block.date == base:
        return ''
    if block.date == base + timedelta(days=1):
        return '翌'
    return f'{block.date.month}/{block.date.day} '


def format_pair(a: ScheduleBlock, b: ScheduleBlock) -> str:
    """重なっている組を運営向けの 1 行にする。相手が別の日なら日付を付ける"""
    return (
        f'{a.date.strftime("%Y/%m/%d")} '
        f'{a.community_name}（{format_block_range(a)}）と '
        f'{b.community_name}（{_date_prefix(b, a.date)}{format_block_range(b)}）'
    )


def _day_label(block: ScheduleBlock, day: date) -> str:
    """その日から見た枠の時間帯の表示"""
    if block.date < day:
        return f'〜{block.end:%H:%M}（前日から）'
    return format_block_range(block)


def _minutes_since_epoch(value: datetime) -> int:
    return int((value - _EPOCH).total_seconds() // 60)


def busy_payload(blocks: Iterable[ScheduleBlock], buffer_minutes: int = 0) -> dict:
    """申込みフォームの空き表示用のデータを返す。

    blocks は判定用の枠の一覧（1970-01-01 からの分で開始・終了を持つ）、
    days は日付ごとに、入れ替えの間隔込みで一部でもかかる枠の番号と表示を持つ。
    集会名と時間帯だけを含め、それ以外の参加情報は出さない。
    """
    items: list[dict] = []
    days: dict[str, list[dict]] = {}
    gap = timedelta(minutes=buffer_minutes)
    for index, block in enumerate(blocks):
        items.append({
            'name': block.community_name,
            'start_abs': _minutes_since_epoch(block.start_dt),
            'end_abs': _minutes_since_epoch(block.end_dt),
        })
        # 判定用の開始・終了はそのままに、表示だけ前後の入れ替えの時間を含める
        display_start = block.start_dt - gap
        display_block = replace(
            block, date=display_start.date(), start=display_start.time(),
            duration=block.duration + buffer_minutes * 2,
        )
        for day in display_block.touched_dates():
            days.setdefault(day.isoformat(), []).append({
                'index': index,
                'label': _day_label(display_block, day),
            })
    return {'blocks': items, 'days': days}
