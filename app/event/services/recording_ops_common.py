"""録画編集プロキシ・確定指示キューサービスの共通例外と検証ヘルパー。"""
from __future__ import annotations

import copy
import logging
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from community.constants import RecordingPolicy
from community.models import Community
from event.models import EventDetail, RecordingEditOrder, RecordingReviewItem
from event.youtube_urls import youtube_video_id

logger = logging.getLogger(__name__)

DEFAULT_PROXY_RETENTION = timedelta(days=30)
ORDER_LEASE_TIMEOUT = timedelta(minutes=30)
MAX_REVISION = 2_147_483_647
_VIDEO_ID_PATTERN = re.compile(r"\A[0-9A-Za-z_-]{11}\Z")


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
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        video_id = youtube_video_id(url)
    except ValueError as exc:
        raise RecordingValidationError("youtube_url の形式が不正です。") from exc
    if parsed.scheme != "https" or not hostname or parsed.netloc.lower() != hostname:
        raise RecordingValidationError("youtube_url はポートや資格情報を含まない https URL で指定してください。")
    if video_id is None:
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
    """リビジョン番号が bool でない 1 以上 MAX_REVISION 以下の整数であることを検証する。"""
    if type(value) is not int or value < 1 or value > MAX_REVISION:
        raise RecordingValidationError(f"{field_name} は 1 以上 {MAX_REVISION} 以下の整数で指定してください。")
    return value


def lock_event_detail(detail_id: int, *, include_deleted: bool = False) -> EventDetail:
    """Community -> EventDetail の統一順で select_for_update ロックして取得する。"""
    community_id = (
        EventDetail.all_objects.filter(pk=detail_id)
        .values_list("event__community_id", flat=True)
        .first()
    )
    if community_id is None:
        raise RecordingNotFoundError("対象の発表が見つかりません。")
    Community.objects.select_for_update().filter(pk=community_id).first()

    manager = EventDetail.all_objects if include_deleted else EventDetail.objects
    qs = manager.select_for_update(of=("self",)).select_related("event__community")
    try:
        return qs.get(pk=detail_id)
    except EventDetail.DoesNotExist as exc:
        raise RecordingNotFoundError("対象の発表が見つかりません。") from exc


def lock_event_detail_or_none(detail_id: int, *, include_deleted: bool = False) -> EventDetail | None:
    """Community -> EventDetail の統一順でロック取得し、存在しない場合は None を返す。"""
    try:
        return lock_event_detail(detail_id, include_deleted=include_deleted)
    except RecordingNotFoundError:
        return None


def is_recording_consent_valid(detail: EventDetail) -> bool:
    """発表が録画公開同意および集会許可を満たしており未削除かを返す。"""
    return (
        detail.deleted_at is None
        and detail.recording_policy == RecordingPolicy.PUBLIC
        and bool(detail.event.community.recording_allowed)
    )


def item_has_unstopped_video(item: RecordingReviewItem) -> bool:
    """非公開確定済みではなく、停止対象となり得る外部動画または公開状態が存在するか。"""
    if (
        item.review_status == RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
        and item.youtube_draft_privacy == RecordingReviewItem.YouTubePrivacy.PRIVATE
    ):
        return False
    return (
        bool(item.youtube_draft_video_id)
        or item.review_status
        in (
            RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED,
            RecordingReviewItem.ReviewStatus.PUBLISHED,
        )
        or item.youtube_draft_privacy
        in (
            RecordingReviewItem.YouTubePrivacy.UNLISTED,
            RecordingReviewItem.YouTubePrivacy.PUBLIC,
        )
    )


def enqueue_compensating_stop_publish(
    *,
    detail: EventDetail,
    item: RecordingReviewItem,
    requested_by: Any = None,
    source_order_id: int | None = None,
) -> RecordingEditOrder:
    """同意撤回・論理削除等に際して補償 stop_publish 指示を自動投入する。"""
    base_revision = item.edl_revision
    if base_revision < MAX_REVISION:
        target_revision = base_revision + 1
        item.edl_revision = target_revision
        item.save(update_fields=["edl_revision", "updated_at"])
    else:
        target_revision = base_revision
    compensating = RecordingEditOrder.objects.create(
        review_item=item,
        event_detail=detail,
        order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
        base_revision=base_revision,
        target_revision=target_revision,
        edl_snapshot=copy.deepcopy(item.edl_json or {}),
        status=RecordingEditOrder.Status.QUEUED,
        active_lock_key=f"item:{item.pk}",
        requested_by=requested_by if getattr(requested_by, "is_authenticated", False) else None,
    )
    logger.warning(
        "recording_order_compensating_stop_publish_queued",
        extra={
            "event_type": "recording_order_compensating_stop_publish_queued",
            "order_id": compensating.pk,
            "source_order_id": source_order_id,
            "event_detail_id": detail.pk,
        },
    )
    return compensating


def is_order_lease_expired(order: RecordingEditOrder, *, now: datetime) -> bool:
    """running 状態のオーダーがリース期限を超過しているか判定する。"""
    if order.status != RecordingEditOrder.Status.RUNNING:
        return False
    return order.started_at is None or order.started_at <= now - ORDER_LEASE_TIMEOUT


def mark_order_lease_expired(
    order: RecordingEditOrder,
    *,
    now: datetime,
    error_message: str = "ワーカーのリース期限（30分）を超過したため自動失敗終了しました。",
    event_detail_id: int | None = None,
) -> None:
    """リース期限切れの running オーダーを failed に遷移させて排他キーを解放する。"""
    order.status = RecordingEditOrder.Status.FAILED
    order.active_lock_key = None
    order.completed_at = now
    order.error_message = error_message
    order.save(
        update_fields=[
            "status",
            "active_lock_key",
            "completed_at",
            "error_message",
            "updated_at",
        ]
    )
    logger.warning(
        "recording_order_lease_expired",
        extra={
            "event_type": "recording_order_lease_expired",
            "order_id": order.pk,
            "event_detail_id": event_detail_id if event_detail_id is not None else order.event_detail_id,
            "order_type": order.order_type,
        },
    )
