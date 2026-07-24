"""定期イベントの永続化処理

`RecurrenceService.create_recurring_events` のロジックを切り出したモジュール。
日付リストからマスターイベント＋インスタンス群を Event テーブルに作成する。
"""
import datetime
from datetime import date
from typing import List

from event.models import Event, RecurrenceRule


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

    if not dates:
        return created_events

    # マスターイベントを作成（最初の日付）
    master_event = Event.objects.create(
        community=community,
        date=dates[0],
        start_time=start_time,
        duration=duration,
        weekday=dates[0].strftime('%a').upper()[:3],
        recurrence_rule=rule,
        is_recurring_master=True,
    )
    created_events.append(master_event)

    # 残りのインスタンスを作成
    for event_date in dates[1:]:
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
                weekday=event_date.strftime('%a').upper()[:3],
                recurring_master=master_event,
            )
            created_events.append(event)

    return created_events
