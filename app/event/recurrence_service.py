"""recurrence_service 互換シム (後方互換性のため re-export)

新規コードは `from event.recurrence import RecurrenceService` を使うこと。

ここで `Event` も re-export しているのは、既存テストが
`patch("event.recurrence_service.Event.objects.filter", ...)` の形で
モックしているため。`event.models.Event` と同一オブジェクトを指すので、
実装が `event/recurrence/llm_generator.py` 側にあっても patch は効く。

`event.recurrence_service` 名のロガーも維持。実際の出力は
`llm_generator.py` 内の `logging.getLogger("event.recurrence_service")` から行う。
"""
import logging

from event.models import Event  # noqa: F401  (patch 用に re-export)
from event.recurrence import RecurrenceService  # noqa: F401
from event.recurrence.calculator import (  # noqa: F401  (旧 import 互換)
    get_japanese_weekday,
    get_nth_weekday_of_month,
    get_week_of_month,
)
from event.recurrence.parser import (  # noqa: F401  (旧 import 互換)
    WEEK_TOKEN_MAP,
    WEEKDAY_TOKEN_MAP,
)

logger = logging.getLogger(__name__)

__all__ = ["RecurrenceService"]
