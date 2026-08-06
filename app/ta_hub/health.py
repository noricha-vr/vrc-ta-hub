"""ヘルスチェックエンドポイント

Cloud Run の readiness / liveness probe 用の軽量エンドポイント。
zombie プロセスへの誤ルーティングを防ぐため、DB と cache の疎通を確認する。

設計方針:
- DB 失敗は致命的なので 503 を返す（probe で外れる）
- cache 失敗は status を ng にしない（cache 未設定でも生存判定したい）
- shared_cache は default alias（Cloud Run では DatabaseCache）の可用性を別に返す。
  cache フィールドは常に LocMem の healthcheck alias を見るため、DatabaseCache の
  テーブル未作成（migration 未適用）を検知できない。デプロイ前チェックはこちらを見る。
"""

from django.core.cache import caches
from django.db import connection
from django.http import JsonResponse

# cache の往復確認に使うキーと値（短い TTL で残骸を残さない）
_HEALTH_CACHE_KEY = "_health_probe"
_HEALTH_CACHE_VALUE = "1"
_HEALTH_CACHE_TTL_SEC = 5


def _get_health_cache():
    """現在のsettingsからhealth probe専用cacheを取得する。"""
    return caches['healthcheck']


def health_check(request):
    """軽量ヘルスチェック (DB + cache ping)

    Returns:
        JsonResponse: ``{"status", "db", "cache", "shared_cache"}`` の各値が
            ``"ok"|"ng"``。DB がダウンしている場合のみ 503、それ以外は 200。
    """
    checks = {"status": "ok"}

    try:
        connection.ensure_connection()
        checks["db"] = "ok"
    except Exception:
        checks["db"] = "ng"
        checks["status"] = "ng"

    try:
        health_cache = _get_health_cache()
        health_cache.set(_HEALTH_CACHE_KEY, _HEALTH_CACHE_VALUE, _HEALTH_CACHE_TTL_SEC)
        if health_cache.get(_HEALTH_CACHE_KEY) != _HEALTH_CACHE_VALUE:
            raise RuntimeError("cache roundtrip failed")
        checks["cache"] = "ok"
    except Exception:
        # cache 未設定環境でも生存判定したいので status は ng にしない
        checks["cache"] = "ng"

    try:
        # default alias は Cloud Run では DatabaseCache。probe ごとに INSERT すると
        # Cloud SQL への書き込みが増えるので、読み取りだけで疎通を確かめる
        # （テーブルが無ければここで例外になる）。
        caches['default'].get(_HEALTH_CACHE_KEY)
        checks["shared_cache"] = "ok"
    except Exception:
        # レート制限は落ちるがページ表示は生きうるので status は ng にしない
        checks["shared_cache"] = "ng"

    status_code = 200 if checks["status"] == "ok" else 503
    return JsonResponse(checks, status=status_code)
