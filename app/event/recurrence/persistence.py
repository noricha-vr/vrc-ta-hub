"""定期イベントの永続化処理

`RecurrenceService.create_recurring_events` のロジックを切り出したモジュール。
日付リストからマスターイベント＋インスタンス群を Event テーブルに作成する。
"""
import datetime
from datetime import date
from typing import List

from community.constants import weekday_code
from event.models import Event, RecurrenceRule
from event.services.recurrence_override import exclude_tombstoned_dates


def create_recurring_events(
    community,
    rule: RecurrenceRule,
    dates: List[date],
    start_time: datetime.time,
    duration: int,
) -> List[Event]:
    """与えられた日付リストから定期イベントのインスタンスを作成して返す。

    Args:
        community: Community インスタンス
        rule: RecurrenceRule インスタンス
        dates: 開催日リスト（昇順想定）
        start_time: 開始時刻
        duration: 所要時間（分）

    Returns:
        作成された Event のリスト（マスター + インスタンス群）
    """
    created_events: List[Event] = []
    dates = exclude_tombstoned_dates(community, dates)

    if not dates:
        return created_events

    # マスター候補も子と同様に既存イベント日を避ける。
    # tombstone で初回が除外され、2回目以降がDBに残っている状態でルールを作り直すと
    # event_unique_community_date_start_time に衝突して生成全体が失敗するため。
    existing_dates = set(
        Event.objects.filter(
            community=community,
            date__in=dates,
        ).values_list('date', flat=True)
    )
    master_date = next(
        (candidate for candidate in dates if candidate not in existing_dates),
        None,
    )
    if master_date is None:
        return created_events

    # マスターイベントを作成（既存イベントのない最初の日付）
    master_event = Event.objects.create(
        community=community,
        date=master_date,
        start_time=start_time,
        duration=duration,
        weekday=weekday_code(master_date),
        recurrence_rule=rule,
        is_recurring_master=True,
    )
    created_events.append(master_event)

    # 残りのインスタンスを作成
    for event_date in dates:
        if event_date == master_date:
            continue
        # 既存のイベントがあるかチェック
        # 開始時刻を編集済みのイベントを重複生成しないため date 単位で判定
        existing = Event.objects.filter(
            community=community,
            date=event_date,
        ).first()

        if not existing:
            event = Event.objects.create(
                community=community,
                date=event_date,
                start_time=start_time,
                duration=duration,
                weekday=weekday_code(event_date),
                recurring_master=master_event,
            )
            created_events.append(event)

    return created_events
