"""Vket コラボの参加枠の重なり判定。

主催者の申込み検証・申込みフォームの空き表示・運営の日程画面の警告・
公開同期前の検査は、すべてこのモジュールの関数で判定する。
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import combinations

from .models import VketCollaboration, VketParticipation

# 開催時間が未入力の参加を判定・表示するときの仮の長さ（日程表の既存表示と揃える）
DEFAULT_DURATION_MINUTES = 60
SCHEDULE_BUFFER_SETTING_KEY = 'schedule_buffer_minutes'
MAX_SCHEDULE_BUFFER_MINUTES = 120

_BASE_DATE = date(2000, 1, 1)


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
    def end(self) -> time:
        """終了時刻（日跨ぎは時刻だけを返す）"""
        return (datetime.combine(_BASE_DATE, self.start) + timedelta(minutes=self.duration)).time()


def get_schedule_buffer_minutes(collaboration: VketCollaboration) -> int:
    """コラボの入れ替えの間隔（分）を返す。不正値は 0 とみなす"""
    raw = (collaboration.settings_json or {}).get(SCHEDULE_BUFFER_SETTING_KEY, 0)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 0
    return min(max(raw, 0), MAX_SCHEDULE_BUFFER_MINUTES)


def set_schedule_buffer_minutes(collaboration: VketCollaboration, minutes: int) -> None:
    """入れ替えの間隔を settings_json の他のキーを保ったまま保存する"""
    collaboration.settings_json = {
        **(collaboration.settings_json or {}),
        SCHEDULE_BUFFER_SETTING_KEY: minutes,
    }
    collaboration.save(update_fields=['settings_json', 'updated_at'])


def ranges_conflict(
    start1: time,
    duration1: int,
    start2: time,
    duration2: int,
    buffer_minutes: int = 0,
) -> bool:
    """2 つの時間帯が、前後に間隔を足したうえで重なるかを返す"""
    s1 = datetime.combine(_BASE_DATE, start1)
    e1 = s1 + timedelta(minutes=duration1)
    s2 = datetime.combine(_BASE_DATE, start2)
    e2 = s2 + timedelta(minutes=duration2)
    gap = timedelta(minutes=buffer_minutes)
    return s1 < e2 + gap and s2 < e1 + gap


def blocks_conflict(a: ScheduleBlock, b: ScheduleBlock, buffer_minutes: int = 0) -> bool:
    """同じ日で、間隔を含めて時間帯が重なる 2 つの枠なら True"""
    if a.date != b.date:
        return False
    return ranges_conflict(a.start, a.duration, b.start, b.duration, buffer_minutes)


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


def active_blocks(
    collaboration: VketCollaboration,
    *,
    exclude_community_id: int | None = None,
) -> list[ScheduleBlock]:
    """コラボ内の有効な参加（取り消し・辞退を除く）の枠を日付・開始時刻順に返す"""
    qs = (
        VketParticipation.objects.filter(
            collaboration=collaboration,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
        )
        .select_related('community')
    )
    if exclude_community_id is not None:
        qs = qs.exclude(community_id=exclude_community_id)
    blocks = [block for p in qs if (block := block_for(p)) is not None]
    return sorted(blocks, key=lambda b: (b.date, b.start, b.community_name))


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
    ordered = sorted(blocks, key=lambda b: (b.date, b.start, b.community_name))
    return [
        (a, b)
        for a, b in combinations(ordered, 2)
        if a.community_id != b.community_id and blocks_conflict(a, b, buffer_minutes)
    ]


def format_pair(a: ScheduleBlock, b: ScheduleBlock) -> str:
    """重なっている組を運営向けの 1 行にする"""
    return (
        f'{a.date.strftime("%Y/%m/%d")} '
        f'{a.community_name}（{a.start:%H:%M}〜{a.end:%H:%M}）と '
        f'{b.community_name}（{b.start:%H:%M}〜{b.end:%H:%M}）'
    )


def busy_blocks_by_date(
    collaboration: VketCollaboration,
    *,
    exclude_community_id: int | None,
) -> dict[str, list[dict[str, str | int]]]:
    """申込みフォームの空き表示用に、日付ごとの埋まっている時間帯を返す。

    集会名と時間帯だけを返し、それ以外の参加情報は含めない。
    """
    result: dict[str, list[dict[str, str | int]]] = {}
    for block in active_blocks(collaboration, exclude_community_id=exclude_community_id):
        start_minutes = block.start.hour * 60 + block.start.minute
        result.setdefault(block.date.isoformat(), []).append({
            'start': f'{block.start:%H:%M}',
            'end': f'{block.end:%H:%M}',
            'start_minutes': start_minutes,
            'end_minutes': start_minutes + block.duration,
            'name': block.community_name,
        })
    return result
