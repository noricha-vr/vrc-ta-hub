"""ヘルスチェックエンドポイント

Cloud Run の readiness / liveness probe 用の軽量エンドポイント。
zombie プロセスへの誤ルーティングを防ぐため、DB と cache の疎通を確認する。

設計方針:
- DB 失敗は致命的なので 503 を返す（probe で外れる）
- cache 失敗は status を ng にしない（cache 未設定でも生存判定したい）
"""

from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse

# cache の往復確認に使うキーと値（短い TTL で残骸を残さない）
_HEALTH_CACHE_KEY = "_health_probe"
_HEALTH_CACHE_VALUE = "1"
_HEALTH_CACHE_TTL_SEC = 5


def health_check(request):
    """軽量ヘルスチェック (DB + cache ping)

    Returns:
        JsonResponse: ``{"status": "ok"|"ng", "db": "ok"|"ng", "cache": "ok"|"ng"}``。
            DB がダウンしている場合のみ 503、それ以外は 200。
    """
    checks = {"status": "ok"}

    try:
        connection.ensure_connection()
        checks["db"] = "ok"
    except Exception:
        checks["db"] = "ng"
        checks["status"] = "ng"

    try:
        cache.set(_HEALTH_CACHE_KEY, _HEALTH_CACHE_VALUE, _HEALTH_CACHE_TTL_SEC)
        if cache.get(_HEALTH_CACHE_KEY) != _HEALTH_CACHE_VALUE:
            raise RuntimeError("cache roundtrip failed")
        checks["cache"] = "ok"
    except Exception:
        # cache 未設定環境でも生存判定したいので status は ng にしない
        checks["cache"] = "ng"

    status_code = 200 if checks["status"] == "ok" else 503
    return JsonResponse(checks, status=status_code)
