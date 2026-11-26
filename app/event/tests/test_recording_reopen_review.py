"""期限切れ非公開確定（private_fixed）からの reopen_review オーダー（#979）のテスト。"""
from __future__ import annotations

from datetime import date, timedelta
import os
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from event.models import EventDetail, RecordingEditOrder, RecordingReviewItem
from event.services.recording_ops.completion import complete_edit_order
from event.services.recording_ops.errors import RecordingConflictError
from event.services.recording_ops.order_actions import retry_edit_order
from event.services.recording_ops.reopen import enqueue_reopen_review, evaluate_reopen_eligibility
from event.services.recording_ops.stop_queue import cancel_queued_apply_edl_and_compensate
from event.services.recording_ops.storage import RecordingConsentError
from event.services.recording_ops.worker import claim_next_edit_order
from tests.factories import make_community, make_event, make_event_detail, make_user


def _edl() -> dict:
    return {
        "schema_version": 1,
        "safe_bounds": {"min_sec": 0.0, "max_sec": 120.0},
        "trim": {"start_sec": 0.0, "end_sec": 60.0},
        "cuts": [],
        "boundary_candidates": {"start": [], "end": []},
    }


class RecordingReopenReviewTest(TestCase):
    def setUp(self):
        self.superuser = make_user(
            user_name="ro_super", email="ro_super@example.com", is_staff=True, is_superuser=True
        )
        self.staff = make_user(
            user_name="ro_staff", email="ro_staff@example.com", is_staff=True, is_superuser=False
        )
        self.community = make_community(name="復帰テスト集会", status="approved", recording_allowed=True)
        self.event = make_event(self.community, event_date=date(2026, 11, 26))
        self.detail = make_event_detail(
            self.event,
            detail_type="LT",
            status="approved",
            speaker="登壇者",
            theme="テーマ",
            recording_policy=EventDetail.RecordingPolicy.PUBLIC,
        )
        self.now = timezone.now()
        self.item = RecordingReviewItem.objects.create(
            event_detail=self.detail,
            proxy_r2_key=f"recording-proxy/{self.detail.pk}/0123456789abcdef0123456789abcdef.mp4",
            proxy_duration_sec=120.0,
            proxy_expires_at=self.now + timedelta(days=10),
            review_status=RecordingReviewItem.ReviewStatus.PRIVATE_FIXED,
            edl_revision=2,
            worker_synced_revision=2,
            edl_json=_edl(),
            youtube_draft_video_id="dQw4w9WgXcQ",
            youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.PRIVATE,
        )
        self.apply_order = RecordingEditOrder.objects.create(
            review_item=self.item,
            event_detail=self.detail,
            order_type=RecordingEditOrder.OrderType.APPLY_EDL,
            base_revision=1,
            target_revision=2,
            edl_snapshot=_edl(),
            status=RecordingEditOrder.Status.SUCCEEDED,
            result_video_id="dQw4w9WgXcQ",
        )

    def test_feature_flag_and_superuser_only(self):
        # 1. フラグ OFF（既定）は 409 reopen_disabled
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RECORDING_OPS_REOPEN_ENABLED", None)
            with self.assertRaises(RecordingConflictError) as ctx:
                enqueue_reopen_review(self.detail.pk, expected_revision=2, requested_by=self.superuser)
            self.assertEqual(ctx.exception.code, "reopen_disabled")

        # 2. スタッフは 403（オーナー限定）
        with mock.patch.dict(os.environ, {"RECORDING_OPS_REOPEN_ENABLED": "true"}):
            with self.assertRaises(RecordingConsentError):
                enqueue_reopen_review(self.detail.pk, expected_revision=2, requested_by=self.staff)

    def test_reopen_enqueue_claim_complete_and_retry_forbidden(self):
        with mock.patch.dict(os.environ, {"RECORDING_OPS_REOPEN_ENABLED": "true"}):
            order = enqueue_reopen_review(self.detail.pk, expected_revision=2, requested_by=self.superuser)

        self.item.refresh_from_db()
        # 登録時に review_status は processing に変えず private_fixed のまま
        self.assertEqual(self.item.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)
        self.assertEqual(self.item.edl_revision, 3)
        self.assertEqual(order.target_video_id, "dQw4w9WgXcQ")
        self.assertEqual(order.order_type, RecordingEditOrder.OrderType.REOPEN_REVIEW)

        # claim -> complete (private + needs_review) -> worker_synced_revision=3 に同期
        claimed = claim_next_edit_order(order_types=[RecordingEditOrder.OrderType.REOPEN_REVIEW])
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.pk, order.pk)
        complete_edit_order(
            order.pk,
            target_revision=3,
            status=RecordingEditOrder.Status.SUCCEEDED,
            result_video_id="dQw4w9WgXcQ",
            youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.PRIVATE,
            review_status=RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
        )
        self.item.refresh_from_db()
        self.assertEqual(self.item.review_status, RecordingReviewItem.ReviewStatus.NEEDS_REVIEW)
        self.assertEqual(self.item.youtube_draft_privacy, RecordingReviewItem.YouTubePrivacy.PRIVATE)
        self.assertEqual(self.item.worker_synced_revision, 3)

    def test_failed_reopen_does_not_block_re_enqueue_and_retry_is_forbidden(self):
        with mock.patch.dict(os.environ, {"RECORDING_OPS_REOPEN_ENABLED": "true"}):
            order1 = enqueue_reopen_review(self.detail.pk, expected_revision=2, requested_by=self.superuser)
            claimed = claim_next_edit_order(order_types=[RecordingEditOrder.OrderType.REOPEN_REVIEW])
            self.assertEqual(claimed.pk, order1.pk)
            complete_edit_order(order1.pk, target_revision=3, status=RecordingEditOrder.Status.FAILED, error_message="err")

            # retry_edit_order は拒否
            with self.assertRaises(RecordingConflictError) as ctx:
                retry_edit_order(self.detail.pk, order1.pk, expected_revision=3, requested_by=self.superuser)
            self.assertEqual(ctx.exception.code, "reopen_not_retryable")

            # 失敗した reopen_review 自身は最新オーダー判定から除外されるため再起票可能！
            allowed, code, _ = evaluate_reopen_eligibility(self.detail, self.item)
            self.assertTrue(allowed, code)
            order2 = enqueue_reopen_review(self.detail.pk, expected_revision=3, requested_by=self.superuser)
            self.assertEqual(order2.target_revision, 4)

    def test_ineligible_cases_blocked_by_allowlist(self):
        # 1. stop_publish 履歴あり（CANCELED でも不可）
        stop = RecordingEditOrder.objects.create(
            review_item=self.item,
            event_detail=self.detail,
            order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
            base_revision=1,
            target_revision=2,
            status=RecordingEditOrder.Status.CANCELED,
        )
        allowed, code, _ = evaluate_reopen_eligibility(self.detail, self.item)
        self.assertFalse(allowed)
        self.assertEqual(code, "stop_publish_history_exists")
        stop.delete()

        # 2. プロキシ残り 24 時間未満
        self.item.proxy_expires_at = timezone.now() + timedelta(hours=12)
        self.item.save(update_fields=["proxy_expires_at"])
        allowed, code, _ = evaluate_reopen_eligibility(self.detail, self.item)
        self.assertFalse(allowed)
        self.assertEqual(code, "proxy_expiring_or_expired")
        self.item.proxy_expires_at = timezone.now() + timedelta(days=10)
        self.item.save(update_fields=["proxy_expires_at"])

        # 3. EDL 不一致
        self.item.edl_json = {**_edl(), "trim": {"start_sec": 5.0, "end_sec": 60.0}}
        self.item.save(update_fields=["edl_json"])
        allowed, code, _ = evaluate_reopen_eligibility(self.detail, self.item)
        self.assertFalse(allowed)
        self.assertEqual(code, "edl_mismatch")

    def test_revocation_cancels_queued_reopen_and_running_reopen_stays_private_fixed(self):
        with mock.patch.dict(os.environ, {"RECORDING_OPS_REOPEN_ENABLED": "true"}):
            order1 = enqueue_reopen_review(self.detail.pk, expected_revision=2, requested_by=self.superuser)
        # 待機中に同意撤回 -> 自動キャンセル
        cancel_queued_apply_edl_and_compensate(detail=self.detail, item=self.item, now=timezone.now())
        order1.refresh_from_db()
        self.assertEqual(order1.status, RecordingEditOrder.Status.CANCELED)

        # 実行中に同意撤回 -> compensating_stop_pending=True になり完了しても private_fixed のまま閉じる
        with mock.patch.dict(os.environ, {"RECORDING_OPS_REOPEN_ENABLED": "true"}):
            order2 = enqueue_reopen_review(self.detail.pk, expected_revision=3, requested_by=self.superuser)
        claimed = claim_next_edit_order(order_types=[RecordingEditOrder.OrderType.REOPEN_REVIEW])
        self.assertEqual(claimed.pk, order2.pk)
        self.detail.recording_policy = EventDetail.RecordingPolicy.FORBIDDEN
        self.detail.save(update_fields=["recording_policy"])
        cancel_queued_apply_edl_and_compensate(detail=self.detail, item=self.item, now=timezone.now())
        self.item.refresh_from_db()
        self.assertTrue(self.item.compensating_stop_pending)

        complete_edit_order(
            order2.pk,
            target_revision=4,
            status=RecordingEditOrder.Status.SUCCEEDED,
            result_video_id="dQw4w9WgXcQ",
            youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.PRIVATE,
            review_status=RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
        )
        self.item.refresh_from_db()
        self.assertEqual(self.item.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)
        self.assertFalse(self.item.compensating_stop_pending)
