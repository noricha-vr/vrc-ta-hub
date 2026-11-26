"""YouTube 動画公開完了の事実を RecordingReviewItem へ冪等に同期するサービス（Layer 3）。"""
from __future__ import annotations

import logging
from typing import Any

from django.db import transaction

from event.models import RecordingEditOrder, RecordingReviewItem

from .errors import (
    RecordingConflictError,
    RecordingValidationError,
    serialize_item_state,
    validate_video_id,
)
from .locks import is_recording_consent_valid, lock_event_detail
from .stop_queue import (
    add_pending_stop_video_id,
    clear_detail_youtube_url,
    enqueue_compensating_stop_publish,
)

logger = logging.getLogger(__name__)

_ALLOWED_MARK_PUBLISHED_STATUSES = frozenset(
    {
        RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
        RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED,
    }
)
_ALLOWED_MARK_PUBLISHED_PRIVACIES = frozenset(
    {
        "",
        RecordingReviewItem.YouTubePrivacy.UNLISTED,
        RecordingReviewItem.YouTubePrivacy.PRIVATE,
    }
)


def mark_recording_item_published(detail_id: int, *, video_id: str) -> dict[str, Any]:
    """YouTube 公開完了の事実を RecordingReviewItem に記録する。

    - プロキシ未登録の発表は 200 冪等スキップ（synced=False）。
    - すでに published / public / 同一 video_id なら 200 冪等成功（already_published=True）。
    - 同意無効・論理削除済みの場合は公開事実を記録しつつ補償 stop_publish を起票し、
      発表ページの youtube_url を消去した上で 409（consent_revoked_stop_enqueued）を送出する。
    - 許可リスト外の不整合（進行中オーダー・別動画 ID・private_fixed 等）は 409 で拒否する。
    """
    if not isinstance(video_id, str) or not video_id.strip():
        raise RecordingValidationError("video_id は必須です。")
    normalized_video_id = validate_video_id(video_id.strip(), "video_id")

    with transaction.atomic():
        # ロック順序は既存サービスと揃えて EventDetail -> RecordingReviewItem とする。
        detail = lock_event_detail(detail_id, include_deleted=True)
        item = RecordingReviewItem.objects.select_for_update().filter(event_detail=detail).first()
        if item is None:
            logger.info(
                "recording_ops_mark_published",
                extra={
                    "event_type": "recording_ops_mark_published",
                    "event_detail_id": detail.pk,
                    "result": "no_review_item",
                },
            )
            return {
                "event_detail_id": detail.pk,
                "synced": False,
                "already_published": False,
                "compensating_stop_enqueued": False,
                "reason": "no_review_item",
                "review_status": "",
                "is_public_locked": False,
            }

        consent_valid = detail.deleted_at is None and is_recording_consent_valid(detail)
        active_order = (
            RecordingEditOrder.objects.select_for_update()
            .filter(review_item=item, status__in=RecordingEditOrder.ACTIVE_STATUSES)
            .first()
        )

        # 1. 同意撤回・論理削除レース: YouTube 公開済みの事実を取りこぼさず補償停止を積む。
        if not consent_valid:
            item_fields = ["updated_at"]
            if item.youtube_draft_video_id and item.youtube_draft_video_id != normalized_video_id:
                if add_pending_stop_video_id(item, item.youtube_draft_video_id):
                    item_fields.append("pending_stop_video_ids")
            if add_pending_stop_video_id(item, normalized_video_id):
                item_fields.append("pending_stop_video_ids")
            if item.youtube_draft_video_id != normalized_video_id:
                item.youtube_draft_video_id = normalized_video_id
                item_fields.append("youtube_draft_video_id")
            if item.youtube_draft_privacy != RecordingReviewItem.YouTubePrivacy.PUBLIC:
                item.youtube_draft_privacy = RecordingReviewItem.YouTubePrivacy.PUBLIC
                item_fields.append("youtube_draft_privacy")
            if item.review_status != RecordingReviewItem.ReviewStatus.PUBLISHED:
                item.review_status = RecordingReviewItem.ReviewStatus.PUBLISHED
                item_fields.append("review_status")
            clear_detail_youtube_url(detail)

            if active_order is None:
                item.save(update_fields=list(dict.fromkeys(item_fields)))
                enqueue_compensating_stop_publish(
                    detail=detail,
                    item=item,
                    target_video_id=normalized_video_id,
                )
            else:
                if not item.compensating_stop_pending:
                    item.compensating_stop_pending = True
                    item_fields.append("compensating_stop_pending")
                item.save(update_fields=list(dict.fromkeys(item_fields)))

            logger.warning(
                "recording_ops_mark_published",
                extra={
                    "event_type": "recording_ops_mark_published",
                    "event_detail_id": detail.pk,
                    "result": "consent_revoked_stop_enqueued",
                },
            )
            raise RecordingConflictError(
                "撮影同意が無効または発表が削除済みのため、補償公開停止を登録しました。",
                code="consent_revoked_stop_enqueued",
                current_state=serialize_item_state(item),
            )

        # 2. 冪等成功: すでに同一動画で published / public 済み。
        if (
            item.review_status == RecordingReviewItem.ReviewStatus.PUBLISHED
            and item.youtube_draft_privacy == RecordingReviewItem.YouTubePrivacy.PUBLIC
            and item.youtube_draft_video_id == normalized_video_id
        ):
            if item.public_locked_at is None:
                item.save(update_fields=["public_locked_at", "updated_at"])
            logger.info(
                "recording_ops_mark_published",
                extra={
                    "event_type": "recording_ops_mark_published",
                    "event_detail_id": detail.pk,
                    "result": "already_published",
                },
            )
            return {
                "event_detail_id": detail.pk,
                "synced": True,
                "already_published": True,
                "compensating_stop_enqueued": False,
                "reason": "already_published",
                "review_status": item.review_status,
                "is_public_locked": item.is_public_locked,
            }

        # 3. 許可リスト検証（fail-closed）
        if active_order is not None:
            raise RecordingConflictError(
                "進行中の編集・停止オーダーがあるため公開同期できません。",
                code="active_order_in_progress",
                current_state=serialize_item_state(item),
            )
        if item.review_status not in _ALLOWED_MARK_PUBLISHED_STATUSES:
            raise RecordingConflictError(
                f"現在のレビュー状態（{item.review_status}）からは公開済みへ同期できません。",
                code="invalid_review_status",
                current_state=serialize_item_state(item),
            )
        if item.youtube_draft_privacy not in _ALLOWED_MARK_PUBLISHED_PRIVACIES:
            raise RecordingConflictError(
                f"現在の下書き公開範囲（{item.youtube_draft_privacy}）からは公開済みへ同期できません。",
                code="invalid_draft_privacy",
                current_state=serialize_item_state(item),
            )
        pending_videos = [v for v in (item.pending_stop_video_ids or []) if isinstance(v, str) and v]
        if item.compensating_stop_pending or pending_videos:
            raise RecordingConflictError(
                "未処理の停止待ち動画または補償停止保留があるため公開同期できません。",
                code="pending_stop_exists",
                current_state=serialize_item_state(item),
            )
        if not item.youtube_draft_video_id or item.youtube_draft_video_id != normalized_video_id:
            raise RecordingConflictError(
                "登録済みの下書き動画 ID と公開動画 ID が一致しません。",
                code="video_id_mismatch",
                current_state=serialize_item_state(item),
            )

        # 4. 公開状態へ原子遷移（public_locked_at は RecordingReviewItem.save() が自動設定）
        item.review_status = RecordingReviewItem.ReviewStatus.PUBLISHED
        item.youtube_draft_privacy = RecordingReviewItem.YouTubePrivacy.PUBLIC
        item.save(update_fields=["review_status", "youtube_draft_privacy", "updated_at"])

        logger.info(
            "recording_ops_mark_published",
            extra={
                "event_type": "recording_ops_mark_published",
                "event_detail_id": detail.pk,
                "result": "published",
            },
        )
        return {
            "event_detail_id": detail.pk,
            "synced": True,
            "already_published": False,
            "compensating_stop_enqueued": False,
            "reason": "published",
            "review_status": item.review_status,
            "is_public_locked": item.is_public_locked,
        }
