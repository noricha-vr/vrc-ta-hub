"""記事の自動生成を Cloud Scheduler から少しずつ進めるエンドポイント。"""
import os

from django.http import HttpResponse, JsonResponse
from django.utils.crypto import constant_time_compare
from django.views.decorators.http import require_http_methods

from event.services.article_generation import (
    DEFAULT_BATCH_SIZE,
    MAX_BATCH_SIZE,
    process_article_generation_queue,
)


@require_http_methods(["GET"])
def run_article_generation(request):
    """Cloud Scheduler から 1 分ごとに呼ばれ、生成待ちの記事を 1〜2 件作る。

    認証は予約投稿（twitter.views.post_scheduled_tweets）と同じ Request-Token ヘッダー。
    """
    request_token = request.headers.get("Request-Token", "")
    expected = os.environ.get("REQUEST_TOKEN", "")
    if not expected or not constant_time_compare(request_token, expected):
        return HttpResponse("Unauthorized", status=401)

    limit = _parse_limit(request.GET.get("limit"))
    if limit is None:
        return JsonResponse(
            {"error": f"limit must be an integer between 1 and {MAX_BATCH_SIZE}"},
            status=400,
        )
    return JsonResponse(process_article_generation_queue(limit=limit))


def _parse_limit(raw: str | None) -> int | None:
    """1 回に処理する件数。未指定は既定値、範囲外や数字でない時は None。"""
    if raw in (None, ""):
        return DEFAULT_BATCH_SIZE
    try:
        limit = int(raw)
    except ValueError:
        return None
    if not 1 <= limit <= MAX_BATCH_SIZE:
        return None
    return limit
