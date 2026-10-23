"""発表申請の開始時刻計算。"""

from collections.abc import Iterable
from datetime import datetime, time, timedelta


def _calc_lt_start_time(event_start_time: time, offset_minutes: int) -> time:
    """集会の start_time に offset(分) を加算した time を返す。

    datetime.time は加算非対応のため、datetime.combine で日付を仮置きして演算する。
    23:50 + 30分 → 00:20 のように 24h を跨ぐケースも循環する（Community.end_time と同じ慣例）。
    """
    base = datetime.combine(datetime.today(), event_start_time)
    return (base + timedelta(minutes=offset_minutes)).time()


def _calc_next_lt_start_time(
    event_start_time: time,
    existing_lt_slots: Iterable[tuple[time, int]],
    offset_minutes: int,
) -> time:
    """既存LTの終了後、またはオフセット後の次の開始時刻を返す。

    開始時刻との差分は24時間で循環し、循環後の差分が12時間を超える場合は
    イベント開始前に手動配置されたLTとして負の経過時間に補正する。
    """
    slots = list(existing_lt_slots)
    if not slots:
        return _calc_lt_start_time(event_start_time, offset_minutes)

    event_start_minutes = event_start_time.hour * 60 + event_start_time.minute
    slot_end_minutes = []
    for start_time, duration in slots:
        wrapped_delta = (
            start_time.hour * 60 + start_time.minute - event_start_minutes
        ) % (24 * 60)
        delta_minutes = (
            wrapped_delta - (24 * 60)
            if wrapped_delta > 12 * 60
            else wrapped_delta
        )
        slot_end_minutes.append(delta_minutes + duration)

    latest_end_minutes = max(slot_end_minutes)
    return _calc_lt_start_time(
        event_start_time,
        max(latest_end_minutes, offset_minutes),
    )
