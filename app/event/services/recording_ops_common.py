"""録画編集プロキシ・確定指示キューサービスの共通例外と検証ヘルパー。"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from event.models import EventDetail, RecordingReviewItem
from event.youtube_urls import youtube_video_id

DEFAULT_PROXY_RETENTION = timedelta(days=30)
_VIDEO_ID_PATTERN = re.compile(r"\A[0-9A-Za-z_-]{1,32}\Z")


class RecordingOpsError(Exception):
    """録画編集オペレーションサービスの基底例外。"""

    code = "recording_ops_error"

    def __init__(self, detail: str, *, code: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        if code is not None:
            self.code = code


class RecordingValidationError(RecordingOpsError):
    """リクエスト値が仕様を満たさない。"""

    code = "validation_error"


class RecordingNotFoundError(RecordingOpsError):
    """対象レコードが存在しない。"""

    code = "not_found"


class RecordingConflictError(RecordingOpsError):
    """楽観ロックまたは排他状態による競合（HTTP 409）。"""

    code = "conflict"

    def __init__(
        self,
        detail: str,
        *,
        code: str = "conflict",
        current_state: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail, code=code)
        self.current_state = current_state


def serialize_item_state(item: RecordingReviewItem) -> dict[str, Any]:
    """409 競合レスポンス等に含める現在のレビュー状態スナップショット。"""
    return {
        "event_detail_id": item.event_detail_id,
        "review_status": item.review_status,
        "edl_revision": item.edl_revision,
        "edl_json": item.edl_json,
        "youtube_draft_video_id": item.youtube_draft_video_id,
        "youtube_draft_privacy": item.youtube_draft_privacy,
        "is_public_locked": item.is_public_locked,
    }


def validate_safe_youtube_url(url: str) -> str:
    """EventDetail.youtube_url へ反映してよい安全な https YouTube URL か検証する。"""
    if not isinstance(url, str) or not url or len(url) > 200:
        raise RecordingValidationError("youtube_url は 200 文字以内の有効な URL で指定してください。")
    if any(ch.isspace() for ch in url):
        raise RecordingValidationError("youtube_url に空白や改行を含めることはできません。")
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not hostname or parsed.netloc.lower() != hostname:
        raise RecordingValidationError("youtube_url はポートや資格情報を含まない https URL で指定してください。")
    if youtube_video_id(url) is None:
        raise RecordingValidationError("youtube_url は有効な YouTube 動画 URL である必要があります。")
    return url


def validate_video_id(video_id: str, field_name: str) -> str:
    """YouTube 動画 ID の文字種と長さを検証する。"""
    if not isinstance(video_id, str):
        raise RecordingValidationError(f"{field_name} は文字列で指定してください。")
    if video_id and _VIDEO_ID_PATTERN.fullmatch(video_id) is None:
        raise RecordingValidationError(f"{field_name} の形式が不正です。")
    return video_id


def validate_positive_revision(value: Any, field_name: str) -> int:
    """リビジョン番号が bool でない 1 以上の整数であることを検証する。"""
    if type(value) is not int or value < 1:
        raise RecordingValidationError(f"{field_name} は 1 以上の整数で指定してください。")
    return value


def lock_event_detail(detail_id: int, *, include_deleted: bool = False) -> EventDetail:
    """EventDetail 行を select_for_update で排他ロックして取得する。"""
    manager = EventDetail.all_objects if include_deleted else EventDetail.objects
    qs = manager.select_for_update(of=("self",)).select_related("event__community")
    try:
        return qs.get(pk=detail_id)
    except EventDetail.DoesNotExist as exc:
        raise RecordingNotFoundError("対象の発表が見つかりません。") from exc
