"""動画編集プロキシメタ情報および EDL v1 状態の登録・更新サービス。"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from event.models import RecordingEditOrder, RecordingReviewItem
from event.services.edl import EDLValidationError, validate_edl_v1
from event.services.recording_ops_common import (
    DEFAULT_PROXY_RETENTION,
    RecordingConflictError,
    RecordingValidationError,
    lock_event_detail,
    serialize_item_state,
    validate_positive_revision,
    validate_video_id,
)
from event.services.recording_storage import (
    ensure_recording_consent,
    validate_proxy_object_key,
    validate_transcript_object_key,
)

logger = logging.getLogger(__name__)

_ALLOWED_UPSERT_PRIVACIES = frozenset(
    {
        "",
        RecordingReviewItem.YouTubePrivacy.UNLISTED,
        RecordingReviewItem.YouTubePrivacy.PRIVATE,
    }
)
_ALLOWED_UPSERT_STATUSES = frozenset(
    {
        RecordingReviewItem.ReviewStatus.PROCESSING,
        RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
        RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED,
    }
)


def _validate_upsert_inputs(
    detail_id: int,
    *,
    proxy_r2_key: str | None,
    transcript_r2_key: str | None,
    proxy_duration_sec: float | None,
    review_status: str | None,
    edl_json: dict[str, Any] | None,
    youtube_draft_video_id: str | None,
    youtube_draft_privacy: str | None,
    expected_revision: int | None,
) -> tuple[float | None, dict[str, Any] | None]:
    if expected_revision is not None:
        validate_positive_revision(expected_revision, "expected_revision")
    normalized_duration: float | None = None
    if proxy_duration_sec is not None:
        if isinstance(proxy_duration_sec, bool) or not isinstance(proxy_duration_sec, (int, float)):
            raise RecordingValidationError("proxy_duration_sec は数値で指定してください。")
        normalized_duration = float(proxy_duration_sec)
        if not math.isfinite(normalized_duration) or normalized_duration <= 0:
            raise RecordingValidationError("proxy_duration_sec は 0 より大きい有限の数値で指定してください。")
    if youtube_draft_video_id is not None:
        validate_video_id(youtube_draft_video_id, "youtube_draft_video_id")
    if youtube_draft_privacy is not None and youtube_draft_privacy not in _ALLOWED_UPSERT_PRIVACIES:
        raise RecordingValidationError("youtube_draft_privacy は '', unlisted, private のいずれかで指定してください。")
    if review_status is not None and review_status not in _ALLOWED_UPSERT_STATUSES:
        raise RecordingValidationError(
            "review_status は processing / needs_review / unlisted_scheduled のいずれかで指定してください。"
        )
    if proxy_r2_key is not None:
        validate_proxy_object_key(detail_id, proxy_r2_key)
    if transcript_r2_key is not None and transcript_r2_key != "":
        validate_transcript_object_key(detail_id, transcript_r2_key)

    validated_edl: dict[str, Any] | None = None
    if edl_json is not None:
        try:
            validated_edl = validate_edl_v1(edl_json)
        except EDLValidationError as exc:
            raise RecordingValidationError(str(exc)) from exc
    return normalized_duration, validated_edl


def upsert_recording_item(
    detail_id: int,
    *,
    proxy_r2_key: str | None = None,
    transcript_r2_key: str | None = None,
    proxy_duration_sec: float | None = None,
    proxy_uploaded_at: datetime | None = None,
    proxy_expires_at: datetime | None = None,
    review_status: str | None = None,
    edl_json: dict[str, Any] | None = None,
    youtube_draft_video_id: str | None = None,
    youtube_draft_privacy: str | None = None,
    expected_revision: int | None = None,
) -> tuple[RecordingReviewItem, bool]:
    """プロキシメタ情報と EDL v1 を登録または更新する。"""
    normalized_duration, validated_edl = _validate_upsert_inputs(
        detail_id,
        proxy_r2_key=proxy_r2_key,
        transcript_r2_key=transcript_r2_key,
        proxy_duration_sec=proxy_duration_sec,
        review_status=review_status,
        edl_json=edl_json,
        youtube_draft_video_id=youtube_draft_video_id,
        youtube_draft_privacy=youtube_draft_privacy,
        expected_revision=expected_revision,
    )

    with transaction.atomic():
        detail = lock_event_detail(detail_id)
        ensure_recording_consent(detail, action="upsert_item")
        item = RecordingReviewItem.objects.select_for_update().filter(event_detail=detail).first()
        now = timezone.now()

        if item is None:
            if expected_revision is not None and expected_revision != 1:
                raise RecordingConflictError(
                    "初回登録時の expected_revision が一致しません。",
                    code="revision_conflict",
                )
            if not proxy_r2_key:
                raise RecordingValidationError("初回登録では proxy_r2_key が必須です。")
            if validated_edl is None:
                raise RecordingValidationError("初回登録では edl_json が必須です。")

            uploaded_at = proxy_uploaded_at or now
            expires_at = proxy_expires_at or (uploaded_at + DEFAULT_PROXY_RETENTION)
            privacy = youtube_draft_privacy or ""
            initial_status = review_status or (
                RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED
                if privacy == RecordingReviewItem.YouTubePrivacy.UNLISTED
                else RecordingReviewItem.ReviewStatus.NEEDS_REVIEW
            )
            item = RecordingReviewItem.objects.create(
                event_detail=detail,
                proxy_r2_key=proxy_r2_key,
                transcript_r2_key=transcript_r2_key or "",
                proxy_duration_sec=normalized_duration,
                proxy_uploaded_at=uploaded_at,
                proxy_expires_at=expires_at,
                review_status=initial_status,
                edl_revision=1,
                edl_json=validated_edl,
                youtube_draft_video_id=youtube_draft_video_id or "",
                youtube_draft_privacy=privacy,
            )
            logger.info(
                "recording_item_upserted",
                extra={
                    "event_type": "recording_item_upserted",
                    "event_detail_id": detail.pk,
                    "is_created": True,
                    "edl_revision": item.edl_revision,
                    "review_status": item.review_status,
                },
            )
            return item, True

        if item.is_public_locked:
            logger.warning(
                "recording_item_conflict",
                extra={
                    "event_type": "recording_item_conflict",
                    "event_detail_id": detail.pk,
                    "reason": "public_locked",
                },
            )
            raise RecordingConflictError(
                "公開済みの発表は上書きできません。",
                code="public_locked",
                current_state=serialize_item_state(item),
            )
        if RecordingEditOrder.objects.filter(
            review_item=item,
            status__in=RecordingEditOrder.ACTIVE_STATUSES,
        ).exists():
            logger.warning(
                "recording_item_conflict",
                extra={
                    "event_type": "recording_item_conflict",
                    "event_detail_id": detail.pk,
                    "reason": "active_order_exists",
                },
            )
            raise RecordingConflictError(
                "進行中の確定指示があるため更新できません。",
                code="active_order_exists",
                current_state=serialize_item_state(item),
            )
        if expected_revision is None or expected_revision != item.edl_revision:
            logger.warning(
                "recording_item_conflict",
                extra={
                    "event_type": "recording_item_conflict",
                    "event_detail_id": detail.pk,
                    "reason": "revision_conflict",
                },
            )
            raise RecordingConflictError(
                "expected_revision が現在のリビジョンと一致しません。",
                code="revision_conflict",
                current_state=serialize_item_state(item),
            )

        update_fields = ["edl_revision", "updated_at"]
        item.edl_revision += 1

        if proxy_r2_key is not None:
            proxy_changed = proxy_r2_key != item.proxy_r2_key
            item.proxy_r2_key = proxy_r2_key
            update_fields.append("proxy_r2_key")
            if proxy_changed or proxy_uploaded_at is not None:
                item.proxy_uploaded_at = proxy_uploaded_at or now
                update_fields.append("proxy_uploaded_at")
            if proxy_expires_at is not None:
                item.proxy_expires_at = proxy_expires_at
                update_fields.append("proxy_expires_at")
            elif proxy_changed:
                base_uploaded_at = item.proxy_uploaded_at or now
                item.proxy_expires_at = base_uploaded_at + DEFAULT_PROXY_RETENTION
                update_fields.append("proxy_expires_at")
        else:
            if proxy_uploaded_at is not None:
                item.proxy_uploaded_at = proxy_uploaded_at
                update_fields.append("proxy_uploaded_at")
            if proxy_expires_at is not None:
                item.proxy_expires_at = proxy_expires_at
                update_fields.append("proxy_expires_at")

        if transcript_r2_key is not None:
            item.transcript_r2_key = transcript_r2_key
            update_fields.append("transcript_r2_key")
        if normalized_duration is not None:
            item.proxy_duration_sec = normalized_duration
            update_fields.append("proxy_duration_sec")
        if validated_edl is not None:
            item.edl_json = validated_edl
            update_fields.append("edl_json")
        if youtube_draft_video_id is not None:
            item.youtube_draft_video_id = youtube_draft_video_id
            update_fields.append("youtube_draft_video_id")
        if youtube_draft_privacy is not None:
            item.youtube_draft_privacy = youtube_draft_privacy
            update_fields.append("youtube_draft_privacy")
        if review_status is not None:
            item.review_status = review_status
            update_fields.append("review_status")
        elif item.review_status in (
            RecordingReviewItem.ReviewStatus.UNRECORDED,
            RecordingReviewItem.ReviewStatus.PROCESSING,
            RecordingReviewItem.ReviewStatus.EXPIRED,
        ):
            item.review_status = RecordingReviewItem.ReviewStatus.NEEDS_REVIEW
            update_fields.append("review_status")

        item.save(update_fields=list(dict.fromkeys(update_fields)))
        logger.info(
            "recording_item_upserted",
            extra={
                "event_type": "recording_item_upserted",
                "event_detail_id": detail.pk,
                "is_created": False,
                "edl_revision": item.edl_revision,
                "review_status": item.review_status,
            },
        )
        return item, False
