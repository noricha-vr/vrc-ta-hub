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


ALLOWED_COMPLETE_PRIVACIES = frozenset(
    {
        "",
        RecordingReviewItem.YouTubePrivacy.UNLISTED,
        RecordingReviewItem.YouTubePrivacy.PRIVATE,
        RecordingReviewItem.YouTubePrivacy.PUBLIC,
    }
)
ALLOWED_COMPLETE_STATUSES = frozenset(
    {
        "",
        RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
        RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED,
        RecordingReviewItem.ReviewStatus.PUBLISHED,
        RecordingReviewItem.ReviewStatus.PRIVATE_FIXED,
    }
)


def derive_review_status_on_success(
    *,
    order_type: str,
    privacy: str,
    requested_review_status: str,
) -> str:
    """完了報告の指示種別・公開設定・要求ステータスから整合するレビュー状態を決定する。"""
    if order_type == RecordingEditOrder.OrderType.STOP_PUBLISH:
        if privacy not in ("", RecordingReviewItem.YouTubePrivacy.PRIVATE):
            raise RecordingValidationError("stop_publish の完了報告では youtube_draft_privacy='private' のみ指定できます。")
        if requested_review_status not in ("", RecordingReviewItem.ReviewStatus.PRIVATE_FIXED):
            raise RecordingValidationError("stop_publish の完了報告では review_status='private_fixed' のみ指定できます。")
        return RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
    if (
        requested_review_status == RecordingReviewItem.ReviewStatus.PUBLISHED
        and privacy != RecordingReviewItem.YouTubePrivacy.PUBLIC
    ):
        raise RecordingValidationError("published に遷移するには youtube_draft_privacy='public' が必要です。")
    if (
        privacy == RecordingReviewItem.YouTubePrivacy.PUBLIC
        and requested_review_status
        and requested_review_status != RecordingReviewItem.ReviewStatus.PUBLISHED
    ):
        raise RecordingValidationError("youtube_draft_privacy='public' のときは review_status='published' を指定してください。")
    if (
        requested_review_status == RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
        and privacy != RecordingReviewItem.YouTubePrivacy.PRIVATE
    ):
        raise RecordingValidationError("private_fixed に遷移するには youtube_draft_privacy='private' が必要です。")
    if (
        privacy == RecordingReviewItem.YouTubePrivacy.PRIVATE
        and requested_review_status
        and requested_review_status != RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
    ):
        raise RecordingValidationError("youtube_draft_privacy='private' のときは review_status='private_fixed' を指定してください。")
    if (
        privacy == RecordingReviewItem.YouTubePrivacy.PUBLIC
        or requested_review_status == RecordingReviewItem.ReviewStatus.PUBLISHED
    ):
        return RecordingReviewItem.ReviewStatus.PUBLISHED
    if requested_review_status:
        return requested_review_status
    if privacy == RecordingReviewItem.YouTubePrivacy.PRIVATE:
        return RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
    return RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED


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


def add_pending_stop_video_id(item: RecordingReviewItem, video_id: str) -> bool:
    """停止待ち動画 ID 一覧に未登録の video_id を追加し、変更があれば True を返す。"""
    if not video_id:
        return False
    pending = [str(v) for v in (item.pending_stop_video_ids or []) if isinstance(v, str) and v]
    if video_id in pending:
        return False
    pending.append(video_id)
    item.pending_stop_video_ids = pending
    return True


def remove_pending_stop_video_id(item: RecordingReviewItem, *video_ids: str) -> bool:
    """停止が完了した video_id を停止待ち動画 ID 一覧から除去する。"""
    remove_set = {v for v in video_ids if v}
    if not remove_set:
        return False
    current = [str(v) for v in (item.pending_stop_video_ids or []) if isinstance(v, str) and v]
    updated = [v for v in current if v not in remove_set]
    if len(updated) == len(current):
        return False
    item.pending_stop_video_ids = updated
    return True


def next_stop_target_video_id(item: RecordingReviewItem) -> str:
    """次に stop_publish が停止対象とすべき動画 ID を返す。"""
    for v in item.pending_stop_video_ids or []:
        if isinstance(v, str) and v:
            return v
    return item.youtube_draft_video_id or ""


def item_has_unstopped_video(item: RecordingReviewItem) -> bool:
    """非公開確定済みではなく、停止対象となり得る外部動画または公開状態が存在するか。"""
    if [v for v in (item.pending_stop_video_ids or []) if isinstance(v, str) and v]:
        return True
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
    detail: EventDetail | None,
    item: RecordingReviewItem,
    target_video_id: str | None = None,
    requested_by: Any = None,
    source_order_id: int | None = None,
) -> RecordingEditOrder:
    """同意撤回・論理削除等に際して補償 stop_publish 指示を自動投入する。"""
    stop_target = target_video_id if target_video_id is not None else next_stop_target_video_id(item)
    item_fields: list[str] = []
    if stop_target and add_pending_stop_video_id(item, stop_target):
        item_fields.append("pending_stop_video_ids")
    base_revision = item.edl_revision
    if base_revision < MAX_REVISION:
        target_revision = base_revision + 1
        item.edl_revision = target_revision
        item_fields.append("edl_revision")
    else:
        target_revision = base_revision
    if item_fields:
        item_fields.append("updated_at")
        item.save(update_fields=item_fields)
    compensating = RecordingEditOrder.objects.create(
        review_item=item,
        event_detail=detail,
        order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
        target_video_id=stop_target,
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
            "event_detail_id": detail.pk if detail is not None else None,
        },
    )
    return compensating


def cancel_queued_apply_edl_and_compensate(
    *,
    detail: EventDetail | None,
    item: RecordingReviewItem,
    now: datetime,
    target_detail_for_order: EventDetail | None = None,
    fallback_requested_by: Any = None,
    source_order_id: int | None = None,
    force_stop_if_idle: bool = False,
) -> bool:
    """待機中の apply_edl を取り消し、他に進行中オーダーが無ければ補償 stop_publish を投入する。"""
    active_orders = list(
        RecordingEditOrder.objects.select_for_update().filter(
            review_item=item,
            status__in=RecordingEditOrder.ACTIVE_STATUSES,
        )
    )
    has_remaining_active = False
    requested_by = fallback_requested_by
    for active in active_orders:
        if (
            active.order_type == RecordingEditOrder.OrderType.APPLY_EDL
            and active.status == RecordingEditOrder.Status.QUEUED
        ):
            active.status = RecordingEditOrder.Status.CANCELED
            active.active_lock_key = None
            active.completed_at = now
            active.error_message = "同意撤回または削除により待機中の apply_edl を自動キャンセルしました。"
            active.save(
                update_fields=[
                    "status",
                    "active_lock_key",
                    "completed_at",
                    "error_message",
                    "updated_at",
                ]
            )
            if requested_by is None:
                requested_by = active.requested_by
        else:
            has_remaining_active = True

    if not has_remaining_active and (force_stop_if_idle or item_has_unstopped_video(item)):
        enqueue_compensating_stop_publish(
            detail=target_detail_for_order,
            item=item,
            target_video_id=next_stop_target_video_id(item),
            requested_by=requested_by,
            source_order_id=source_order_id,
        )
        return True
    return False


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
