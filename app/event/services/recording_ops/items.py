"""動画編集プロキシメタ情報および EDL v1 状態の登録・更新サービス（Layer 3）。"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, NoReturn

from django.db import transaction
from django.utils import timezone

from event.models import RecordingEditOrder, RecordingReviewItem

from .edl import EDLValidationError, validate_edl_v1
from .errors import (
    DEFAULT_PROXY_RETENTION,
    MAX_REVISION,
    RecordingConflictError,
    RecordingValidationError,
    serialize_item_state,
    validate_positive_duration,
    validate_positive_revision,
    validate_status_privacy_pair,
    validate_video_id,
)
from .locks import lock_event_detail
from .stop_queue import preserve_unstopped_video_id
from .storage import (
    ensure_recording_consent,
    validate_proxy_object_key,
    validate_transcript_object_key,
)

logger = logging.getLogger(__name__)

_ALLOWED_UPSERT_PRIVACIES = frozenset(
    {"", RecordingReviewItem.YouTubePrivacy.UNLISTED, RecordingReviewItem.YouTubePrivacy.PRIVATE}
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
    normalized_duration = (
        validate_positive_duration(proxy_duration_sec, "proxy_duration_sec")
        if proxy_duration_sec is not None
        else None
    )
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
    if normalized_duration is not None and validated_edl is not None:
        if float(validated_edl["safe_bounds"]["max_sec"]) > normalized_duration:
            raise RecordingValidationError("safe_bounds.max_sec は proxy_duration_sec 以下で指定してください。")
    return normalized_duration, validated_edl


def _create_recording_item_locked(
    *,
    detail: Any,
    now: datetime,
    proxy_r2_key: str | None,
    transcript_r2_key: str | None,
    normalized_duration: float | None,
    proxy_uploaded_at: datetime | None,
    proxy_expires_at: datetime | None,
    review_status: str | None,
    validated_edl: dict[str, Any] | None,
    youtube_draft_video_id: str | None,
    youtube_draft_privacy: str | None,
    expected_revision: int | None,
) -> RecordingReviewItem:
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
    validate_status_privacy_pair(initial_status, privacy)
    return RecordingReviewItem.objects.create(
        event_detail=detail,
        proxy_r2_key=proxy_r2_key,
        transcript_r2_key=transcript_r2_key or "",
        proxy_duration_sec=normalized_duration,
        proxy_uploaded_at=uploaded_at,
        worker_synced_revision=1,
        proxy_expires_at=expires_at,
        review_status=initial_status,
        edl_revision=1,
        edl_json=validated_edl,
        youtube_draft_video_id=youtube_draft_video_id or "",
        youtube_draft_privacy=privacy,
    )


def _raise_item_conflict(
    *,
    detail_id: int,
    item: RecordingReviewItem,
    reason: str,
    message: str,
) -> NoReturn:
    logger.warning(
        "recording_item_conflict",
        extra={
            "event_type": "recording_item_conflict",
            "event_detail_id": detail_id,
            "reason": reason,
        },
    )
    raise RecordingConflictError(
        message,
        code=reason,
        current_state=serialize_item_state(item),
    )


def _assert_item_updatable(
    *,
    detail_id: int,
    item: RecordingReviewItem,
    expected_revision: int | None,
) -> None:
    if item.is_public_locked:
        _raise_item_conflict(
            detail_id=detail_id,
            item=item,
            reason="public_locked",
            message="公開済みの発表は上書きできません。",
        )
    if RecordingEditOrder.objects.filter(
        review_item=item,
        status__in=RecordingEditOrder.ACTIVE_STATUSES,
    ).exists():
        _raise_item_conflict(
            detail_id=detail_id,
            item=item,
            reason="active_order_exists",
            message="進行中の確定指示があるため更新できません。",
        )
    if expected_revision is None or expected_revision != item.edl_revision:
        _raise_item_conflict(
            detail_id=detail_id,
            item=item,
            reason="revision_conflict",
            message="expected_revision が現在のリビジョンと一致しません。",
        )
    if item.edl_revision >= MAX_REVISION:
        raise RecordingConflictError(
            "EDL リビジョン上限に達しています。",
            code="revision_limit_exceeded",
            current_state=serialize_item_state(item),
        )


def _apply_item_proxy_and_edl_fields(
    item: RecordingReviewItem,
    update_fields: list[str],
    *,
    now: datetime,
    proxy_r2_key: str | None,
    transcript_r2_key: str | None,
    normalized_duration: float | None,
    proxy_uploaded_at: datetime | None,
    proxy_expires_at: datetime | None,
    validated_edl: dict[str, Any] | None,
) -> None:
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
    effective_duration = normalized_duration if normalized_duration is not None else item.proxy_duration_sec
    if validated_edl is not None:
        if effective_duration is not None and float(validated_edl["safe_bounds"]["max_sec"]) > effective_duration:
            raise RecordingValidationError("safe_bounds.max_sec は proxy_duration_sec 以下で指定してください。")
        item.edl_json = validated_edl
        update_fields.append("edl_json")
    elif effective_duration is not None and isinstance(item.edl_json, dict) and "safe_bounds" in item.edl_json:
        max_sec = float(item.edl_json["safe_bounds"]["max_sec"])
        if max_sec > effective_duration:
            raise RecordingValidationError("proxy_duration_sec は登録済みの safe_bounds.max_sec 以上で指定してください。")


def _apply_item_video_and_status_fields(
    item: RecordingReviewItem,
    update_fields: list[str],
    *,
    youtube_draft_video_id: str | None,
    youtube_draft_privacy: str | None,
    review_status: str | None,
) -> None:
    is_video_changed = (
        youtube_draft_video_id is not None and youtube_draft_video_id != item.youtube_draft_video_id
    )
    if youtube_draft_video_id is not None:
        prior_vid = item.youtube_draft_video_id
        if preserve_unstopped_video_id(item, prior_vid, youtube_draft_video_id):
            update_fields.append("pending_stop_video_ids")
        item.youtube_draft_video_id = youtube_draft_video_id
        update_fields.append("youtube_draft_video_id")
    if youtube_draft_privacy is not None:
        item.youtube_draft_privacy = youtube_draft_privacy
        update_fields.append("youtube_draft_privacy")
    elif is_video_changed and item.youtube_draft_privacy:
        item.youtube_draft_privacy = ""
        update_fields.append("youtube_draft_privacy")
    if review_status is not None:
        item.review_status = review_status
        update_fields.append("review_status")
        if (
            review_status != RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
            and youtube_draft_privacy is None
            and item.youtube_draft_privacy == RecordingReviewItem.YouTubePrivacy.PRIVATE
        ):
            item.youtube_draft_privacy = ""
            update_fields.append("youtube_draft_privacy")
    elif (is_video_changed and item.review_status == RecordingReviewItem.ReviewStatus.PRIVATE_FIXED) or (
        item.review_status
        in (
            RecordingReviewItem.ReviewStatus.UNRECORDED,
            RecordingReviewItem.ReviewStatus.PROCESSING,
            RecordingReviewItem.ReviewStatus.EXPIRED,
        )
    ):
        item.review_status = RecordingReviewItem.ReviewStatus.NEEDS_REVIEW
        update_fields.append("review_status")

    validate_status_privacy_pair(item.review_status, item.youtube_draft_privacy)


def _log_item_upserted(*, detail_id: int, item: RecordingReviewItem, is_created: bool) -> None:
    logger.info(
        "recording_item_upserted",
        extra={
            "event_type": "recording_item_upserted",
            "event_detail_id": detail_id,
            "is_created": is_created,
            "edl_revision": item.edl_revision,
            "review_status": item.review_status,
        },
    )


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
            item = _create_recording_item_locked(
                detail=detail,
                now=now,
                proxy_r2_key=proxy_r2_key,
                transcript_r2_key=transcript_r2_key,
                normalized_duration=normalized_duration,
                proxy_uploaded_at=proxy_uploaded_at,
                proxy_expires_at=proxy_expires_at,
                review_status=review_status,
                validated_edl=validated_edl,
                youtube_draft_video_id=youtube_draft_video_id,
                youtube_draft_privacy=youtube_draft_privacy,
                expected_revision=expected_revision,
            )
            _log_item_upserted(detail_id=detail.pk, item=item, is_created=True)
            return item, True

        _assert_item_updatable(detail_id=detail.pk, item=item, expected_revision=expected_revision)

        update_fields = ["edl_revision", "worker_synced_revision", "updated_at"]
        item.edl_revision += 1
        item.worker_synced_revision = item.edl_revision

        _apply_item_proxy_and_edl_fields(
            item,
            update_fields,
            now=now,
            proxy_r2_key=proxy_r2_key,
            transcript_r2_key=transcript_r2_key,
            normalized_duration=normalized_duration,
            proxy_uploaded_at=proxy_uploaded_at,
            proxy_expires_at=proxy_expires_at,
            validated_edl=validated_edl,
        )
        _apply_item_video_and_status_fields(
            item,
            update_fields,
            youtube_draft_video_id=youtube_draft_video_id,
            youtube_draft_privacy=youtube_draft_privacy,
            review_status=review_status,
        )
        item.save(update_fields=list(dict.fromkeys(update_fields)))
        _log_item_upserted(detail_id=detail.pk, item=item, is_created=False)
        return item, False
