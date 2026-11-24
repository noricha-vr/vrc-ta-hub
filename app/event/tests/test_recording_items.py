"""録画レビュー項目登録・更新サービス（items.py / errors.py）の単体テスト。"""
from __future__ import annotations

import math
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from community.constants import RecordingPolicy
from event.models import RecordingEditOrder, RecordingReviewItem
from event.services.recording_ops.edl import validate_edl_v1
from event.services.recording_ops.errors import (
    DEFAULT_PROXY_RETENTION,
    MAX_REVISION,
    RecordingConflictError,
    RecordingNotFoundError,
    RecordingValidationError,
    validate_status_privacy_pair,
)
from event.services.recording_ops.items import upsert_recording_item
from event.services.recording_ops.storage import RecordingConsentError, RecordingObjectKeyError
from tests.factories import make_community, make_event, make_event_detail


def _valid_edl(*, max_sec: float = 600.0) -> dict:
    return {
        "schema_version": 1,
        "safe_bounds": {"min_sec": 0.0, "max_sec": max_sec},
        "trim": {"start_sec": 10.0, "end_sec": min(500.0, max_sec)},
        "boundary_candidates": {"start": [], "end": []},
        "cuts": [],
    }


def _proxy_key(detail_id: int) -> str:
    return f"recording-proxy/{detail_id}/{uuid.uuid4().hex}.mp4"


def _transcript_key(detail_id: int, ext: str = "json") -> str:
    return f"recording-transcript/{detail_id}/{uuid.uuid4().hex}.{ext}"


class RecordingItemsUpsertTest(TestCase):
    def setUp(self):
        self.community = make_community(name="項目テスト集会", recording_allowed=True)
        self.event = make_event(self.community)
        self.detail = make_event_detail(
            self.event,
            status="approved",
            recording_policy=RecordingPolicy.PUBLIC,
        )

    def test_validate_status_privacy_pair_rejects_mismatches(self):
        with self.assertRaises(RecordingValidationError):
            validate_status_privacy_pair(
                RecordingReviewItem.ReviewStatus.PRIVATE_FIXED,
                RecordingReviewItem.YouTubePrivacy.UNLISTED,
            )
        with self.assertRaises(RecordingValidationError):
            validate_status_privacy_pair(
                RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED,
                RecordingReviewItem.YouTubePrivacy.PRIVATE,
            )
        with self.assertRaises(RecordingValidationError):
            validate_status_privacy_pair(
                RecordingReviewItem.ReviewStatus.PUBLISHED,
                RecordingReviewItem.YouTubePrivacy.UNLISTED,
            )

    def test_validate_upsert_inputs_rejects_invalid_values(self):
        key = _proxy_key(self.detail.pk)
        edl = _valid_edl()

        for bad_rev in (True, 0, -1, "1", MAX_REVISION + 1):
            with self.subTest(bad_rev=bad_rev), self.assertRaises(RecordingValidationError):
                upsert_recording_item(
                    self.detail.pk,
                    proxy_r2_key=key,
                    edl_json=edl,
                    expected_revision=bad_rev,  # type: ignore[arg-type]
                )

        for bad_dur in (True, "120", 0, -1.5, math.nan, math.inf, 10**400):
            with self.subTest(bad_dur=bad_dur), self.assertRaises(RecordingValidationError):
                upsert_recording_item(
                    self.detail.pk,
                    proxy_r2_key=key,
                    edl_json=edl,
                    proxy_duration_sec=bad_dur,  # type: ignore[arg-type]
                )

        for bad_vid in (123, "short", "toolongvideoid", "invalid*id!"):
            with self.subTest(bad_vid=bad_vid), self.assertRaises(RecordingValidationError):
                upsert_recording_item(
                    self.detail.pk,
                    proxy_r2_key=key,
                    edl_json=edl,
                    youtube_draft_video_id=bad_vid,  # type: ignore[arg-type]
                )

        with self.assertRaises(RecordingValidationError):
            upsert_recording_item(
                self.detail.pk,
                proxy_r2_key=key,
                edl_json=edl,
                youtube_draft_privacy="public",
            )

        for bad_status in ("published", "private_fixed", "expired", "unknown"):
            with self.subTest(bad_status=bad_status), self.assertRaises(RecordingValidationError):
                upsert_recording_item(
                    self.detail.pk,
                    proxy_r2_key=key,
                    edl_json=edl,
                    review_status=bad_status,
                )

        with self.assertRaises(RecordingObjectKeyError):
            upsert_recording_item(
                self.detail.pk,
                proxy_r2_key=f"recording-proxy/{self.detail.pk + 1}/{uuid.uuid4().hex}.mp4",
                edl_json=edl,
            )

        with self.assertRaises(RecordingObjectKeyError):
            upsert_recording_item(
                self.detail.pk,
                proxy_r2_key=key,
                transcript_r2_key="recording-transcript/invalid.exe",
                edl_json=edl,
            )

        with self.assertRaises(RecordingValidationError):
            upsert_recording_item(
                self.detail.pk,
                proxy_r2_key=key,
                edl_json={"schema_version": 999},
            )

        with self.assertRaises(RecordingValidationError):
            upsert_recording_item(
                self.detail.pk,
                proxy_r2_key=key,
                proxy_duration_sec=500.0,
                edl_json=_valid_edl(max_sec=600.0),
            )

    def test_create_item_defaults_and_required_fields_and_consent_boundary(self):
        key = _proxy_key(self.detail.pk)
        edl = _valid_edl()

        with self.assertRaises(RecordingNotFoundError):
            upsert_recording_item(9999999, proxy_r2_key=_proxy_key(9999999), edl_json=edl)

        with self.assertRaises(RecordingConflictError) as rev_err:
            upsert_recording_item(self.detail.pk, proxy_r2_key=key, edl_json=edl, expected_revision=2)
        self.assertEqual(rev_err.exception.code, "revision_conflict")

        with self.assertRaises(RecordingValidationError):
            upsert_recording_item(self.detail.pk, edl_json=edl)

        with self.assertRaises(RecordingValidationError):
            upsert_recording_item(self.detail.pk, proxy_r2_key=key)

        for policy in (RecordingPolicy.ALLOWED, RecordingPolicy.FORBIDDEN):
            self.detail.recording_policy = policy
            self.detail.save(update_fields=["recording_policy"])
            with self.assertRaises(RecordingConsentError):
                upsert_recording_item(self.detail.pk, proxy_r2_key=key, edl_json=edl)

        self.detail.recording_policy = RecordingPolicy.PUBLIC
        self.detail.save(update_fields=["recording_policy"])

        fixed_now = timezone.now()
        with patch("event.services.recording_ops.items.timezone.now", return_value=fixed_now):
            item, created = upsert_recording_item(
                self.detail.pk,
                proxy_r2_key=key,
                transcript_r2_key=_transcript_key(self.detail.pk, "srt"),
                proxy_duration_sec=700.0,
                edl_json=edl,
                youtube_draft_video_id="dQw4w9WgXcQ",
                youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.UNLISTED,
                expected_revision=1,
            )
        self.assertTrue(created)
        self.assertEqual(item.review_status, RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED)
        self.assertEqual(item.edl_revision, 1)
        self.assertEqual(item.proxy_uploaded_at, fixed_now)
        self.assertEqual(item.proxy_expires_at, fixed_now + DEFAULT_PROXY_RETENTION)

    def test_update_conflicts_public_locked_active_order_revision_and_max_limit(self):
        item, _ = upsert_recording_item(
            self.detail.pk,
            proxy_r2_key=_proxy_key(self.detail.pk),
            proxy_duration_sec=700.0,
            edl_json=_valid_edl(max_sec=600.0),
        )

        # revision_conflict（None および不一致）
        for bad_expected in (None, 2):
            with self.assertRaises(RecordingConflictError) as ctx:
                upsert_recording_item(
                    self.detail.pk,
                    expected_revision=bad_expected,
                    edl_json=_valid_edl(max_sec=600.0),
                )
            self.assertEqual(ctx.exception.code, "revision_conflict")

        # active_order_exists
        order = RecordingEditOrder.objects.create(
            review_item=item,
            event_detail=self.detail,
            base_revision=1,
            target_revision=2,
            status=RecordingEditOrder.Status.QUEUED,
        )
        with self.assertRaises(RecordingConflictError) as ctx:
            upsert_recording_item(self.detail.pk, expected_revision=1, edl_json=_valid_edl(max_sec=600.0))
        self.assertEqual(ctx.exception.code, "active_order_exists")
        order.status = RecordingEditOrder.Status.FAILED
        order.active_lock_key = None
        order.save(update_fields=["status", "active_lock_key"])

        # MAX_REVISION
        item.edl_revision = MAX_REVISION
        item.save(update_fields=["edl_revision"])
        with self.assertRaises(RecordingConflictError) as ctx:
            upsert_recording_item(self.detail.pk, expected_revision=MAX_REVISION, edl_json=_valid_edl(max_sec=600.0))
        self.assertEqual(ctx.exception.code, "revision_limit_exceeded")

        # public_locked
        item.edl_revision = 2
        item.review_status = RecordingReviewItem.ReviewStatus.PUBLISHED
        item.save(update_fields=["edl_revision", "review_status"])
        self.assertTrue(item.is_public_locked)
        with self.assertRaises(RecordingConflictError) as ctx:
            upsert_recording_item(self.detail.pk, expected_revision=2, edl_json=_valid_edl(max_sec=600.0))
        self.assertEqual(ctx.exception.code, "public_locked")

    def test_update_proxy_fields_duration_bounds_and_side_effects(self):
        first_key = _proxy_key(self.detail.pk)
        item, _ = upsert_recording_item(
            self.detail.pk,
            proxy_r2_key=first_key,
            transcript_r2_key=_transcript_key(self.detail.pk),
            proxy_duration_sec=700.0,
            edl_json=_valid_edl(max_sec=600.0),
            youtube_draft_video_id="oldVid00001",
            youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.UNLISTED,
        )
        self.assertEqual(item.edl_revision, 1)

        # 新 proxy_r2_key 指定かつ uploaded_at / expires_at 省略 → 新 uploaded_at と +30日 expires_at が設定される
        second_key = _proxy_key(self.detail.pk)
        t1 = timezone.now() + timedelta(hours=1)
        with patch("event.services.recording_ops.items.timezone.now", return_value=t1):
            item, created = upsert_recording_item(
                self.detail.pk,
                proxy_r2_key=second_key,
                expected_revision=1,
            )
        self.assertFalse(created)
        self.assertEqual(item.edl_revision, 2)
        self.assertEqual(item.proxy_uploaded_at, t1)
        self.assertEqual(item.proxy_expires_at, t1 + DEFAULT_PROXY_RETENTION)

        # 同じ proxy_r2_key で proxy_expires_at のみ明示変更 & transcript クリア
        custom_exp = t1 + timedelta(days=5)
        item, _ = upsert_recording_item(
            self.detail.pk,
            proxy_r2_key=second_key,
            proxy_expires_at=custom_exp,
            transcript_r2_key="",
            expected_revision=2,
        )
        self.assertEqual(item.edl_revision, 3)
        self.assertEqual(item.proxy_expires_at, custom_exp)
        self.assertEqual(item.transcript_r2_key, "")

        # proxy_r2_key=None で uploaded_at / expires_at / duration 更新
        custom_up = t1 + timedelta(hours=2)
        custom_exp2 = custom_up + timedelta(days=10)
        item, _ = upsert_recording_item(
            self.detail.pk,
            proxy_uploaded_at=custom_up,
            proxy_expires_at=custom_exp2,
            proxy_duration_sec=650.0,
            expected_revision=3,
        )
        self.assertEqual(item.proxy_uploaded_at, custom_up)
        self.assertEqual(item.proxy_expires_at, custom_exp2)
        self.assertEqual(item.proxy_duration_sec, 650.0)

        # 既存 safe_bounds.max_sec (600.0) を下回る proxy_duration_sec は拒否される
        with self.assertRaises(RecordingValidationError):
            upsert_recording_item(
                self.detail.pk,
                proxy_duration_sec=500.0,
                expected_revision=4,
            )

        # 新規 edl_json の safe_bounds.max_sec (700.0) が既存 proxy_duration_sec (650.0) を超えると拒否
        with self.assertRaises(RecordingValidationError):
            upsert_recording_item(
                self.detail.pk,
                edl_json=validate_edl_v1(_valid_edl(max_sec=700.0)),
                expected_revision=4,
            )

        # 動画 ID 差し替え: 非停止の oldVid00001 が pending_stop_video_ids へ積まれ、privacy はクリア
        item, _ = upsert_recording_item(
            self.detail.pk,
            youtube_draft_video_id="newVid00002",
            expected_revision=4,
        )
        self.assertEqual(item.pending_stop_video_ids, ["oldVid00001"])
        self.assertEqual(item.youtube_draft_video_id, "newVid00002")
        self.assertEqual(item.youtube_draft_privacy, "")

        # private_fixed + private 状態から新動画 ID に変更しても private_fixed の動画は pending_stop に積まれず、
        # ステータスは needs_review に戻り privacy もクリアされる
        item.review_status = RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
        item.youtube_draft_privacy = RecordingReviewItem.YouTubePrivacy.PRIVATE
        item.save(update_fields=["review_status", "youtube_draft_privacy"])
        item, _ = upsert_recording_item(
            self.detail.pk,
            youtube_draft_video_id="thirdVid003",
            expected_revision=5,
        )
        self.assertEqual(item.pending_stop_video_ids, ["oldVid00001"])
        self.assertEqual(item.review_status, RecordingReviewItem.ReviewStatus.NEEDS_REVIEW)
        self.assertEqual(item.youtube_draft_privacy, "")

        # review_status=PRIVATE_FIXED から明示的に NEEDS_REVIEW へ更新しつつ privacy が PRIVATE の場合もクリア
        item.review_status = RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
        item.youtube_draft_privacy = RecordingReviewItem.YouTubePrivacy.PRIVATE
        item.save(update_fields=["review_status", "youtube_draft_privacy"])
        item, _ = upsert_recording_item(
            self.detail.pk,
            review_status=RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
            expected_revision=6,
        )
        self.assertEqual(item.review_status, RecordingReviewItem.ReviewStatus.NEEDS_REVIEW)
        self.assertEqual(item.youtube_draft_privacy, "")

        # expired / processing / unrecorded から review_status=None で更新すると needs_review に戻る
        for old_status in (
            RecordingReviewItem.ReviewStatus.EXPIRED,
            RecordingReviewItem.ReviewStatus.PROCESSING,
            RecordingReviewItem.ReviewStatus.UNRECORDED,
        ):
            item.review_status = old_status
            item.save(update_fields=["review_status"])
            item, _ = upsert_recording_item(
                self.detail.pk,
                proxy_duration_sec=660.0,
                expected_revision=item.edl_revision,
            )
            self.assertEqual(item.review_status, RecordingReviewItem.ReviewStatus.NEEDS_REVIEW)
