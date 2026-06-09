"""定期イベントの日付生成パッケージ

新規コードからは `from event.recurrence import RecurrenceService` を使う。
旧 `from event.recurrence_service import RecurrenceService` は互換シム経由で
こちらに委譲される。
"""
from event.recurrence.service import RecurrenceService

__all__ = ["RecurrenceService"]
