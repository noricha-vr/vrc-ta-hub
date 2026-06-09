"""API v1 viewset の共通基底クラス。"""

import logging
from typing import Any

from django.db import connections
from django.db.utils import OperationalError
from rest_framework import status
from rest_framework.response import Response

logger = logging.getLogger(__name__)


class DatabaseReconnectListMixin:
    """MySQL の切断系エラー時に読み取り list API を一度だけ再試行する。"""

    MYSQL_DISCONNECT_ERROR_CODES = {2002, 2006, 2013, 2055}
    MYSQL_DISCONNECT_ERROR_MESSAGES = (
        "can't connect",
        "lost connection",
        "reading initial communication packet",
        "server has gone away",
    )

    def list(self, request: Any, *args: Any, **kwargs: Any) -> Response:
        try:
            return super().list(request, *args, **kwargs)
        except OperationalError as exc:
            if not self._should_retry_after_disconnect(exc):
                raise

            logger.warning(
                "Retrying %s.list after a transient database disconnect: %s",
                self.__class__.__name__,
                exc,
            )
            connections.close_all()
            try:
                return super().list(request, *args, **kwargs)
            except OperationalError as retry_exc:
                if not self._should_retry_after_disconnect(retry_exc):
                    raise

                # 再接続後も復旧せず 503 を返す本物の障害なので ERROR (docs/logging.md 規約)
                logger.error(
                    "%s.list returned 503 because the database remained unavailable after reconnect: %s",
                    self.__class__.__name__,
                    retry_exc,
                )
                return Response(
                    {"detail": "Database temporarily unavailable."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

    def _should_retry_after_disconnect(self, exc: OperationalError) -> bool:
        if exc.args:
            code = exc.args[0]
            if isinstance(code, int) and code in self.MYSQL_DISCONNECT_ERROR_CODES:
                return True

        message = str(exc).lower()
        return any(fragment in message for fragment in self.MYSQL_DISCONNECT_ERROR_MESSAGES)
