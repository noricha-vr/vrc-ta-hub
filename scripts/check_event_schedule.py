#!/usr/bin/env python
"""30日間のイベントスケジュールを確認するスクリプト

重複イベントを検出したら非ゼロ終了し、自動化から異常を検知できるようにする。
"""
from __future__ import annotations

import logging
import sys
from collections import Counter
from datetime import timedelta

from _script_bootstrap import setup_django

logger = logging.getLogger(__name__)

CHECK_DAYS = 30


def check_event_schedule() -> int:
    """今日から30日間のイベントを表示し、重複があれば exit code 1 を返す。"""
    from django.utils import timezone

    from event.models import Event

    today = timezone.now().date()
    end_date = today + timedelta(days=CHECK_DAYS)

    logger.info("今日から%d日間のイベントを確認 (%s - %s)", CHECK_DAYS, today, end_date)

    events = Event.objects.filter(
        date__gte=today,
        date__lte=end_date
    ).order_by('date', 'start_time', 'community__name')

    current_date = None
    for event in events:
        if event.date != current_date:
            current_date = event.date
            logger.info("%s (%s):", current_date, current_date.strftime("%A"))

        master_info = ''
        if event.is_recurring_master:
            master_info = ' [MASTER]'
        elif event.recurring_master:
            master_info = f' [Instance of #{event.recurring_master_id}]'

        logger.info("  %s - %s%s", event.start_time, event.community.name, master_info)

    logger.info("合計: %d件", events.count())

    # 重複チェック
    event_keys = [(e.community.name, e.date, e.start_time) for e in events]
    duplicates = [k for k, v in Counter(event_keys).items() if v > 1]
    if not duplicates:
        logger.info("重複イベント: 0件")
        return 0

    logger.error("重複イベント: %d件", len(duplicates))
    for dup in duplicates:
        logger.error("  - %s on %s at %s", dup[0], dup[1], dup[2])
    return 1


def main() -> int:
    setup_django()
    try:
        return check_event_schedule()
    except Exception:
        logger.exception("イベントスケジュールの確認に失敗しました")
        return 1


if __name__ == '__main__':
    sys.exit(main())
