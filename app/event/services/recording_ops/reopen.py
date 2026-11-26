"""期限切れ非公開確定（private_fixed）から確認待ち（needs_review）へ戻す reopen_review サービス（Layer 3）。"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
import logging
import os
from typing import Any

from django.db import transaction
from django.utils import timezone

from event.models import EventDetail, RecordingEditOrder, RecordingReviewItem

from .errors import (
    MAX_REVISION,
    RecordingConflictError,
    serialize_item_state,
    validate_positive_revision,
)
from .locks import is_recording_consent_valid, lock_event_detail
from .orders import _sweep_active_orders
from .storage import RecordingConsentError

logger = logging.getLogger(__name__)

REOPEN_MIN_PROXY_REMAINING = timedelta(hours=24)


def is_reopen_feature_enabled() -> bool:
    """段階リリース用フラグ（既定 False）。レコーダー対応後に True へ切り替える。"""
    return os.environ.get("RECORDING_OPS_REOPEN_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def evaluate_reopen_eligibility(
    detail: EventDetail | None,
    item: RecordingReviewItem | None,
    *,
    now: datetime | None = None,
) -> tuple[bool, str, str]:
    """reopen_review を起票できるかを単一の許可リストで判定する。

    UI 表示・API・サービス層・再試行のすべてで共有する。
    戻り値: (allowed, code, human_reason)
    """
    current_time = now or timezone.now()
    if detail is None or detail.deleted_at is not None or not is_recording_consent_valid(detail):
        return False, "consent_invalid", "撮影公開の同意が有効（承認済み・公開可）ではないため解除できません。"
    if item is None:
        return False, "proxy_not_ready", "編集プロキシが未登録のため解除できません。"
    if item.public_locked_at is not None or item.is_public_locked:
        return False, "public_locked", "公開済みロック（緊急停止含む）のため解除できません。"
    if item.review_status != RecordingReviewItem.ReviewStatus.PRIVATE_FIXED:
        return False, "not_private_fixed", "非公開確定（private_fixed）状態ではないため解除不要です。"
    if item.youtube_draft_privacy != RecordingReviewItem.YouTubePrivacy.PRIVATE:
        return False, "not_private", "YouTube 下書きが非公開（private）ではないため解除できません。"
    if item.compensating_stop_pending or [v for v in (item.pending_stop_video_ids or []) if isinstance(v, str) and v]:
        return False, "pending_stop_exists", "未処理の停止待ち動画または補償停止保留があるため解除できません。"
    if RecordingEditOrder.objects.filter(
        review_item=item,
        status__in=RecordingEditOrder.ACTIVE_STATUSES,
    ).exists():
        return False, "active_order_exists", "進行中の別オーダーがあるため完了後に操作してください。"
    if RecordingEditOrder.objects.filter(
        review_item=item,
        order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
    ).exists():
        return False, "stop_publish_history_exists", "公開停止（stop_publish）の履歴があるため解除できません。"
    if not item.youtube_draft_video_id:
        return False, "missing_video_id", "対象動画 ID が未登録のため解除できません。"
    if item.proxy_expires_at is not None and (item.proxy_expires_at - current_time) < REOPEN_MIN_PROXY_REMAINING:
        return False, "proxy_expiring_or_expired", "編集プロキシの有効期限が 24 時間未満または期限切れのため解除できません。"

    latest_non_reopen = (
        RecordingEditOrder.objects.filter(review_item=item)
        .exclude(status=RecordingEditOrder.Status.CANCELED)
        .exclude(order_type=RecordingEditOrder.OrderType.REOPEN_REVIEW)
        .order_by("-pk")
        .first()
    )
    if (
        latest_non_reopen is None
        or latest_non_reopen.order_type != RecordingEditOrder.OrderType.APPLY_EDL
        or latest_non_reopen.status != RecordingEditOrder.Status.SUCCEEDED
    ):
        return False, "latest_order_not_succeeded_apply_edl", "最新の確定オーダーが成功した apply_edl ではないため解除できません。"
    if latest_non_reopen.result_video_id != item.youtube_draft_video_id:
        return False, "video_id_mismatch", "最新 apply_edl の結果動画 ID と現在の動画 ID が一致しないため解除できません。"
    if latest_non_reopen.edl_snapshot != item.edl_json:
        return False, "edl_mismatch", "最新 apply_edl の EDL と現在の EDL が一致しないため解除できません。"

    return True, "ok", ""


def enqueue_reopen_review(
    detail_id: int,
    *,
    expected_revision: int,
    requested_by: Any = None,
) -> RecordingEditOrder:
    """スーパーユーザー限定で reopen_review オーダーをキューへ登録する（YouTube へは書き込まない）。"""
    validate_positive_revision(expected_revision, "expected_revision")
    if not getattr(requested_by, "is_authenticated", False) or not getattr(requested_by, "is_superuser", False):
        raise RecordingConsentError("確認待ちへの復帰はオーナー（スーパーユーザー）のみ実行できます。")
    if not is_reopen_feature_enabled():
        raise RecordingConflictError(
            "確認待ち復帰機能は現在無効化されています。",
            code="reopen_disabled",
        )

    with transaction.atomic():
        detail = lock_event_detail(detail_id, include_deleted=True)
        item = RecordingReviewItem.objects.select_for_update().filter(event_detail=detail).first()
        if item is None:
            raise RecordingConflictError(
                "編集プロキシが未登録のため解除できません。",
                code="proxy_not_ready",
            )
        if expected_revision != item.edl_revision:
            raise RecordingConflictError(
                "expected_revision が現在の EDL リビジョンと一致しません。",
                code="revision_conflict",
                current_state=serialize_item_state(item),
            )
        now = timezone.now()
        _sweep_active_orders(item=item, now=now, detail_id_for_log=detail.pk)
        allowed, code, reason = evaluate_reopen_eligibility(detail, item, now=now)
        if not allowed:
            raise RecordingConflictError(
                reason,
                code=code,
                current_state=serialize_item_state(item),
            )
        if item.edl_revision >= MAX_REVISION:
            raise RecordingConflictError(
                "EDL リビジョン上限に達しています。",
                code="revision_limit_exceeded",
                current_state=serialize_item_state(item),
            )

        base_revision = item.edl_revision
        target_revision = base_revision + 1
        item.edl_revision = target_revision
        # 登録時に review_status は processing に変えず private_fixed のまま維持する（進行中はバナー表示）。
        item.save(update_fields=["edl_revision", "updated_at"])

        order = RecordingEditOrder.objects.create(
            review_item=item,
            event_detail=detail,
            order_type=RecordingEditOrder.OrderType.REOPEN_REVIEW,
            target_video_id=item.youtube_draft_video_id,
            base_revision=base_revision,
            target_revision=target_revision,
            edl_snapshot=copy.deepcopy(item.edl_json),
            status=RecordingEditOrder.Status.QUEUED,
            active_lock_key=f"item:{item.pk}",
            requested_by=requested_by,
        )

    logger.info(
        "recording_reopen_review_enqueued",
        extra={
            "event_type": "recording_reopen_review_enqueued",
            "order_id": order.pk,
            "event_detail_id": detail_id,
            "target_revision": order.target_revision,
        },
    )
    return order
