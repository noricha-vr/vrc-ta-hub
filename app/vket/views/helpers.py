from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.db.models import Prefetch, Q
from django.utils import timezone

from event.models import EventDetail

from ..forms import VketApplyPermissions
from ..models import (
    VketCollaboration,
    VketParticipation,
    VketPresentation,
)
from ..schedule import (
    block_for,
    blocks_from,
    find_conflicting_pairs,
    format_pair,
    get_schedule_buffer_minutes,
)

logger = logging.getLogger(__name__)

# フェーズの表示順（数値が小さいほど上位に表示）
PHASE_SORT_ORDER = {
    VketCollaboration.Phase.ENTRY_OPEN: 0,
    VketCollaboration.Phase.SCHEDULING: 1,
    VketCollaboration.Phase.LT_COLLECTION: 2,
    VketCollaboration.Phase.ANNOUNCEMENT: 3,
    VketCollaboration.Phase.LOCKED: 4,
    VketCollaboration.Phase.DRAFT: 5,
    VketCollaboration.Phase.ARCHIVED: 6,
}


@dataclass(frozen=True)
class Slot:
    start: time
    end: time


def _is_vket_admin(user) -> bool:
    return user.is_authenticated and (user.is_superuser or user.is_staff)


def _get_active_membership(request):
    """ログインユーザーのアクティブな集会メンバーシップを返す"""
    if not request.user.is_authenticated:
        return None, None

    memberships = request.user.community_memberships.select_related('community')
    if not memberships.exists():
        return None, None

    community_id = request.session.get('active_community_id')
    membership = memberships.filter(community_id=community_id).first() if community_id else None
    if membership is None:
        membership = memberships.first()
        request.session['active_community_id'] = membership.community_id

    return membership.community, membership


def _apply_permissions_for_user(user, collaboration: VketCollaboration) -> VketApplyPermissions:
    """ユーザーのコラボ操作権限を計算して返す"""
    # 管理者（superuser または staff）は全権限を持つ
    if _is_vket_admin(user):
        return VketApplyPermissions(can_edit_schedule=True, can_edit_lt=True)

    today = timezone.localdate()

    # 参加表明（スケジュール）編集: ENTRY_OPENフェーズかつ締切内
    can_edit_schedule = (
        today <= collaboration.registration_deadline
        and collaboration.phase == VketCollaboration.Phase.ENTRY_OPEN
    )

    # LT情報編集: 受付開始後から告知確認中まで、開催期間中は申請として受け付ける
    can_edit_lt = today <= collaboration.period_end and collaboration.phase in {
        VketCollaboration.Phase.ENTRY_OPEN,
        VketCollaboration.Phase.SCHEDULING,
        VketCollaboration.Phase.LT_COLLECTION,
        VketCollaboration.Phase.ANNOUNCEMENT,
    }

    return VketApplyPermissions(can_edit_schedule=can_edit_schedule, can_edit_lt=can_edit_lt)


def _shift_time(t: time, delta: timedelta) -> time:
    """時刻を指定分だけシフトする（日跨ぎはエラー）"""
    base = timezone.localdate()
    dt = datetime.combine(base, t) + delta
    if dt.date() != base:
        raise ValueError('時間の変更が日跨ぎになります。')
    return dt.time()


def _get_visible_collaborations(user):
    """ユーザーに表示可能なコラボ一覧を返す（ARCHIVED除外、非管理者はDRAFTも除外）"""
    qs = VketCollaboration.objects.exclude(phase=VketCollaboration.Phase.ARCHIVED)
    if not _is_vket_admin(user):
        qs = qs.exclude(phase=VketCollaboration.Phase.DRAFT)
    return qs.order_by('-period_start', '-id')


def _build_schedule_context(
    collaboration: VketCollaboration,
    *,
    include_requested: bool = False,
) -> dict:
    """日程表のコンテキストデータを構築する。

    Args:
        collaboration: 対象コラボ
        include_requested: True なら requested_* のみの参加も含める
    """
    empty = {
        'slots': [], 'rows': [], 'overlap_warnings': [], 'warnings': [], 'schedule_blocks': [],
    }

    # クエリ: confirmed があるもの + (オプション) requested のみのもの
    q_confirmed = Q(confirmed_date__isnull=False, confirmed_start_time__isnull=False)
    if include_requested:
        q_requested = Q(requested_date__isnull=False, requested_start_time__isnull=False)
        date_filter = q_confirmed | q_requested
    else:
        date_filter = q_confirmed

    participations = list(
        VketParticipation.objects.filter(
            collaboration=collaboration,
        )
        .filter(date_filter)
        .select_related('community', 'published_event')
        .prefetch_related(
            Prefetch(
                'published_event__details',
                queryset=EventDetail.objects.filter(
                    detail_type='LT', status='approved'
                ).only(
                    'id', 'event_id', 'start_time', 'duration',
                    'speaker', 'theme', 'status', 'detail_type',
                ).order_by('start_time', 'id'),
            ),
            Prefetch(
                'presentations',
                queryset=VketPresentation.objects.only(
                    'id',
                    'participation_id',
                    'order',
                    'requested_start_time',
                    'confirmed_start_time',
                ).order_by('order', 'id'),
            ),
        )
    )

    # 各参加の「表示用」日程を決定（判定と同じ block_for: confirmed 優先、なければ requested）
    blocks_by_pid = {p.id: block for p in participations if (block := block_for(p)) is not None}
    participations = [p for p in participations if p.id in blocks_by_pid]
    if not participations:
        return empty

    effective_data: dict[int, dict] = {
        pid: {
            'date': block.date,
            'start_time': block.start,
            'duration': block.duration,
            'is_confirmed': block.is_confirmed,
        }
        for pid, block in blocks_by_pid.items()
    }

    # date でソート
    participations.sort(key=lambda p: (
        effective_data[p.id]['date'],
        effective_data[p.id]['start_time'],
        p.community.name,
    ))

    slot_minutes = 30

    def to_minutes(t: time) -> int:
        return t.hour * 60 + t.minute

    day_minutes = 24 * 60
    min_start = None
    max_end = None
    warnings: list[str] = []

    lt_times_by_pid: dict[int, list[time]] = {}

    for p in participations:
        eff = effective_data[p.id]
        p_date = eff['date']
        p_start = eff['start_time']
        p_duration = eff['duration']

        start_min = to_minutes(p_start)
        end_min = start_min + p_duration
        if end_min > day_minutes:
            warnings.append(
                f'{p_date.strftime("%Y/%m/%d")} {p.community.name} の開催時間が日跨ぎのため、0:00で切り捨て表示します'
            )
            end_min = day_minutes

        lt_times: list[time] = []
        if p.published_event:
            lt_details = list(p.published_event.details.all())
            lt_times = [d.start_time for d in lt_details] if lt_details else []
        if not lt_times:
            lt_times = [
                start_time
                for pres in p.presentations.all()
                if (start_time := pres.confirmed_start_time or pres.requested_start_time)
            ]
        if not lt_times:
            lt_times = [p_start]
        lt_times_by_pid[p.id] = lt_times

        lt_mins = [to_minutes(t) for t in lt_times]
        lt_min = min(lt_mins)
        lt_end_max = max(min(m + p.lt_slot_minutes, day_minutes) for m in lt_mins)

        candidate_min = min(start_min, lt_min)
        candidate_max = max(end_min, lt_end_max)
        min_start = candidate_min if min_start is None else min(min_start, candidate_min)
        max_end = candidate_max if max_end is None else max(max_end, candidate_max)

    if min_start is None or max_end is None:
        return {**empty, 'warnings': warnings}

    min_start = (min_start // slot_minutes) * slot_minutes
    max_end = ((max_end + slot_minutes - 1) // slot_minutes) * slot_minutes
    max_end = min(max_end, day_minutes)

    slots: list[Slot] = []
    current = min_start
    base = timezone.localdate()
    while current < max_end:
        s_dt = datetime.combine(base, time(hour=current // 60, minute=current % 60))
        e_dt = s_dt + timedelta(minutes=slot_minutes)
        slots.append(Slot(start=s_dt.time(), end=e_dt.time()))
        current += slot_minutes

    def slot_index_for(t: time) -> int | None:
        delta = to_minutes(t) - min_start
        if delta < 0:
            return None
        idx = delta // slot_minutes
        if idx >= len(slots):
            return None
        return idx

    # 重なりの警告と表の赤いセルは、同じ判定（有効な参加だけ・入れ替えの間隔込み）から作る
    buffer_minutes = get_schedule_buffer_minutes(collaboration)
    schedule_blocks = blocks_from(participations)
    conflicting_pairs = find_conflicting_pairs(schedule_blocks, buffer_minutes)
    overlap_warnings = [f'{format_pair(a, b)} が重なっています' for a, b in conflicting_pairs]
    partners_by_pid: dict[int, list] = {}
    for a, b in conflicting_pairs:
        partners_by_pid.setdefault(a.participation_id, []).append(b)
        partners_by_pid.setdefault(b.participation_id, []).append(a)
    gap = timedelta(minutes=buffer_minutes)

    rows = []

    lt_slots_by_pid: dict[int, dict[int, list[time]]] = {}
    lt_slot_communities: dict[tuple, set[str]] = {}

    for p in participations:
        eff = effective_data[p.id]
        p_date = eff['date']
        p_start = eff['start_time']
        p_duration = eff['duration']
        lt_times = lt_times_by_pid[p.id]

        event_start_dt = datetime.combine(base, p_start)
        event_end_dt = event_start_dt + timedelta(minutes=p_duration)

        for lt_time in lt_times:
            idx = slot_index_for(lt_time)
            if idx is None:
                warnings.append(
                    f'{p_date.strftime("%Y/%m/%d")} {p.community.name} の発表開始時刻（{lt_time.strftime("%H:%M")}）が表示範囲外です'
                )
                continue

            lt_slots_by_pid.setdefault(p.id, {}).setdefault(idx, []).append(lt_time)
            key = (p_date, idx)
            lt_slot_communities.setdefault(key, set()).add(p.community.name)

            lt_dt = datetime.combine(base, lt_time)
            if not (event_start_dt <= lt_dt < event_end_dt):
                end_time = event_start_dt + timedelta(minutes=p_duration)
                warnings.append(
                    f'{p_date.strftime("%Y/%m/%d")} {p.community.name} の発表開始時刻（{lt_time.strftime("%H:%M")}）が開催時間（{p_start.strftime("%H:%M")}〜{end_time.strftime("%H:%M")}）の範囲外です'
                )

    for (d, idx), communities in sorted(
        lt_slot_communities.items(), key=lambda x: (x[0][0], x[0][1])
    ):
        if len(communities) <= 1:
            continue
        warnings.append(
            f'{d.strftime("%Y/%m/%d")} {slots[idx].start.strftime("%H:%M")} 発表開始が重複: {", ".join(sorted(communities))}'
        )

    for p in participations:
        eff = effective_data[p.id]
        p_start = eff['start_time']
        p_duration = eff['duration']
        p_date = eff['date']
        event_start = datetime.combine(base, p_start)
        event_end = event_start + timedelta(minutes=p_duration)
        lt_slots = lt_slots_by_pid.get(p.id, {})
        partners = partners_by_pid.get(p.id, [])
        cells = []
        for idx, slot in enumerate(slots):
            slot_start = datetime.combine(base, slot.start)
            slot_end = datetime.combine(base, slot.end)
            occupied = slot_start < event_end and slot_end > event_start
            # 相手の枠（間隔込み）にかかるセルだけを赤くする
            cell_start = datetime.combine(p_date, slot.start)
            cell_end = cell_start + timedelta(minutes=slot_minutes)
            overlap = occupied and any(
                cell_start < q.end_dt + gap and q.start_dt - gap < cell_end for q in partners
            )
            lt_times_in_slot = sorted(lt_slots.get(idx, []))
            lt_overlap = bool(lt_times_in_slot) and len(lt_slot_communities.get((p_date, idx), set())) > 1
            lt_tooltip = ', '.join([t.strftime('%H:%M') for t in lt_times_in_slot]) if lt_times_in_slot else ''
            cells.append({
                'occupied': occupied,
                'overlap': overlap,
                'lt_times': lt_times_in_slot,
                'lt_overlap': lt_overlap,
                'lt_tooltip': lt_tooltip,
            })
        rows.append({
            'participation': p,
            'date': p_date,
            'start_time': p_start,
            'duration': p_duration,
            'is_confirmed': eff['is_confirmed'],
            'cells': cells,
        })

    return {
        'slots': slots,
        'rows': rows,
        'overlap_warnings': overlap_warnings,
        'warnings': warnings,
        'schedule_blocks': schedule_blocks,
    }
