"""Twitter 機能の DB 接続補助。"""

from collections.abc import Callable
import logging
from typing import TypeVar

from django.db import OperationalError, connections

logger = logging.getLogger(__name__)

T = TypeVar("T")

MYSQL_LOST_CONNECTION_ERROR_CODES = {2006, 2013, 2055}
MYSQL_LOST_CONNECTION_MESSAGES = (
    "lost connection",
    "server has gone away",
)


def is_mysql_lost_connection_error(error: OperationalError) -> bool:
    """MySQL 接続断として一度だけ再試行できる OperationalError か判定する。"""
    if error.args:
        code = error.args[0]
        if isinstance(code, int) and code in MYSQL_LOST_CONNECTION_ERROR_CODES:
            return True

    message = str(error).lower()
    return any(fragment in message for fragment in MYSQL_LOST_CONNECTION_MESSAGES)


def run_with_db_reconnect(operation: Callable[[], T], *, context: str) -> T:
    """MySQL 接続断だけを閉じ直して一度だけ再実行する。

    Cloud Scheduler 経由の自動投稿は LLM/X API 待ちを挟むため、次の ORM 操作で
    古い MySQL 接続が autocommit 設定時に 2013 を投げることがある。
    """
    try:
        return operation()
    except OperationalError as error:
        if not is_mysql_lost_connection_error(error):
            raise

        logger.warning(
            "Retrying DB operation after lost MySQL connection: %s",
            context,
        )
        connections.close_all()
        return operation()
