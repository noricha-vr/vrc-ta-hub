"""pending_stop_video_ids と補償 stop_publish 状態機械（stop_queue.py）の単体テスト。"""
from __future__ import annotations

import uuid
from datetime import timedelta

from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from community.constants import RecordingPolicy
from event.models import RecordingEditOrder, RecordingReviewItem
from event.services.recording_ops.errors import (
    MAX_REVISION,
    ORDER_LEASE_TIMEOUT,
    RecordingValidationError,
    derive_review_status_on_success,
    validate_positive_revision,
    validate_safe_youtube_url,
    validate_video_id,
)
from event.services.recording_ops.stop_queue import (
    add_pending_stop_video_id,
    apply_stop_publish_completion,
    cancel_queued_apply_edl_and_compensate,
    clear_detail_youtube_url,
    enqueue_compensating_stop_publish,
    is_order_lease_expired,
    item_has_unstopped_video,
    mark_order_lease_expired,
    next_stop_target_video_id,
    preserve_unstopped_video_id,
    record_partial_or_late_apply_edl_output,
    remove_pending_stop_video_id,
)
from tests.factories import make_community, make_event, make_event_detail, make_user


def _valid_edl() -> dict:
    return {
        "schema_version": 1,
        "safe_bounds": {"min_sec": 0.0, "max_sec": 600.0},
        "trim": {"start_sec": 10.0, "end_sec": 500.0},
        "boundary_candidates": {"start": [], "end": []},
        "cuts": [],
    }


class RecordingStopQueueTest(TestCase):
    def setUp(self):
        self.staff = make_user(user_name="sq_staff", email="sq_staff@example.com", is_staff=True)
        self.community = make_community(name="停止キュー集会", recording_allowed=True)
        self.event = make_event(self.community)
        self.detail = make_event_detail(
            self.event,
            status="approved",
            recording_policy=RecordingPolicy.PUBLIC,
        )
        self.item = RecordingReviewItem.objects.create(
            event_detail=self.detail,
            proxy_r2_key=f"recording-proxy/{self.detail.pk}/{uuid.uuid4().hex}.mp4",
            proxy_duration_sec=600.0,
            review_status=RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
            edl_revision=1,
            edl_json=_valid_edl(),
        )

    def test_pure_validators_in_errors_module(self):
        # derive_review_status_on_success の全エラー分岐
        with self.assertRaises(RecordingValidationError):
            derive_review_status_on_success(
                order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
                privacy=RecordingReviewItem.YouTubePrivacy.PUBLIC,
                requested_review_status="",
            )
        with self.assertRaises(RecordingValidationError):
            derive_review_status_on_success(
                order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
                privacy=RecordingReviewItem.YouTubePrivacy.PRIVATE,
                requested_review_status=RecordingReviewItem.ReviewStatus.PUBLISHED,
            )
        with self.assertRaises(RecordingValidationError):
            derive_review_status_on_success(
                order_type=RecordingEditOrder.OrderType.APPLY_EDL,
                privacy=RecordingReviewItem.YouTubePrivacy.PUBLIC,
                requested_review_status=RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED,
            )
        with self.assertRaises(RecordingValidationError):
            derive_review_status_on_success(
                order_type=RecordingEditOrder.OrderType.APPLY_EDL,
                privacy=RecordingReviewItem.YouTubePrivacy.UNLISTED,
                requested_review_status=RecordingReviewItem.ReviewStatus.PRIVATE_FIXED,
            )
        with self.assertRaises(RecordingValidationError):
            derive_review_status_on_success(
                order_type=RecordingEditOrder.OrderType.APPLY_EDL,
                privacy=RecordingReviewItem.YouTubePrivacy.PRIVATE,
                requested_review_status=RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED,
            )

        # validate_safe_youtube_url / validate_video_id / validate_positive_revision
        for bad_url in (
            "",
            "https://www.youtube.com/watch?v=" + "a" * 200,
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ\\x",
            "https://[::1/invalid",
            "http://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://example.com/watch?v=dQw4w9WgXcQ",
            "https://www.youtube.com/channel/UC1234567890",
        ):
            with self.subTest(bad_url=bad_url), self.assertRaises(RecordingValidationError):
                validate_safe_youtube_url(bad_url)

        with self.assertRaises(RecordingValidationError):
            validate_video_id(123, "vid")  # type: ignore[arg-type]
        with self.assertRaises(RecordingValidationError):
            validate_positive_revision(True, "rev")

    def test_pending_stop_video_id_list_operations_and_unstopped_checks(self):
        # clear_detail_youtube_url
        self.assertFalse(clear_detail_youtube_url(None))
        self.assertFalse(clear_detail_youtube_url(self.detail))
        self.detail.youtube_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        self.detail.save(update_fields=["youtube_url"])
        self.assertTrue(clear_detail_youtube_url(self.detail))
        self.detail.refresh_from_db()
        self.assertIsNone(self.detail.youtube_url)

        # add / remove / preserve / next_stop_target
        self.assertFalse(add_pending_stop_video_id(self.item, ""))
        self.assertTrue(add_pending_stop_video_id(self.item, "vid00000001"))
        self.assertFalse(add_pending_stop_video_id(self.item, "vid00000001"))  # 重複抑止
        self.assertTrue(add_pending_stop_video_id(self.item, "vid00000002"))
        self.assertEqual(next_stop_target_video_id(self.item), "vid00000001")

        self.assertFalse(remove_pending_stop_video_id(self.item, ""))
        self.assertFalse(remove_pending_stop_video_id(self.item, "notPresent1"))
        self.assertTrue(remove_pending_stop_video_id(self.item, "vid00000001"))
        self.assertEqual(self.item.pending_stop_video_ids, ["vid00000002"])
        self.assertTrue(remove_pending_stop_video_id(self.item, "vid00000002"))

        self.item.youtube_draft_video_id = "draftVid001"
        self.assertEqual(next_stop_target_video_id(self.item), "draftVid001")

        # preserve_unstopped_video_id
        self.assertFalse(preserve_unstopped_video_id(self.item, "", "newVid00001"))
        self.assertFalse(preserve_unstopped_video_id(self.item, "draftVid001", "draftVid001"))
        self.assertTrue(preserve_unstopped_video_id(self.item, "draftVid001", "newVid00001"))
        self.assertEqual(self.item.pending_stop_video_ids, ["draftVid001"])

        # PRIVATE_FIXED + PRIVATE のときは積まない & item_has_unstopped_video は pending なしなら False
        self.item.pending_stop_video_ids = []
        self.item.review_status = RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
        self.item.youtube_draft_privacy = RecordingReviewItem.YouTubePrivacy.PRIVATE
        self.assertFalse(preserve_unstopped_video_id(self.item, "draftVid001", "newVid00001"))
        self.assertFalse(item_has_unstopped_video(self.item))

        # pending_stop_video_ids が残っていれば PRIVATE_FIXED でも unstopped=True
        self.item.pending_stop_video_ids = ["stillLive01"]
        self.assertTrue(item_has_unstopped_video(self.item))

    def test_enqueue_compensation_respects_max_revision_and_cancel_states(self):
        now = timezone.now()
        self.item.edl_revision = MAX_REVISION
        self.item.compensating_stop_pending = True
        self.item.youtube_draft_video_id = "maxRevVid01"
        self.item.save(update_fields=["edl_revision", "compensating_stop_pending", "youtube_draft_video_id"])

        comp = enqueue_compensating_stop_publish(
            detail=self.detail,
            item=self.item,
            requested_by=AnonymousUser(),
            source_order_id=123,
        )
        self.item.refresh_from_db()
        self.assertEqual(comp.base_revision, MAX_REVISION)
        self.assertEqual(comp.target_revision, MAX_REVISION)
        self.assertIsNone(comp.requested_by)
        self.assertFalse(self.item.compensating_stop_pending)
        self.assertEqual(self.item.pending_stop_video_ids, ["maxRevVid01"])
        comp.status = RecordingEditOrder.Status.SUCCEEDED
        comp.active_lock_key = None
        comp.save(update_fields=["status", "active_lock_key"])

        # running apply_edl があるときにキャンセル補償を呼ぶと compensating_stop_pending=True になる
        self.item.edl_revision = 5
        self.item.save(update_fields=["edl_revision"])
        running = RecordingEditOrder.objects.create(
            review_item=self.item,
            event_detail=self.detail,
            order_type=RecordingEditOrder.OrderType.APPLY_EDL,
            base_revision=5,
            target_revision=6,
            edl_snapshot=_valid_edl(),
            status=RecordingEditOrder.Status.RUNNING,
            active_lock_key=f"item:{self.item.pk}",
            started_at=now,
        )
        with transaction.atomic():
            enqueued = cancel_queued_apply_edl_and_compensate(
                detail=self.detail,
                item=self.item,
                now=now,
            )
        self.item.refresh_from_db()
        self.assertFalse(enqueued)
        self.assertTrue(self.item.compensating_stop_pending)

        # running が終わり、未停止動画が無くなった状態で再度呼ぶと compensating_stop_pending=False に降りる
        running.status = RecordingEditOrder.Status.FAILED
        running.active_lock_key = None
        running.save(update_fields=["status", "active_lock_key"])
        self.item.pending_stop_video_ids = []
        self.item.youtube_draft_video_id = ""
        self.item.review_status = RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
        self.item.youtube_draft_privacy = RecordingReviewItem.YouTubePrivacy.PRIVATE
        self.item.save(
            update_fields=[
                "pending_stop_video_ids",
                "youtube_draft_video_id",
                "review_status",
                "youtube_draft_privacy",
            ]
        )
        with transaction.atomic():
            enqueued = cancel_queued_apply_edl_and_compensate(
                detail=self.detail,
                item=self.item,
                now=now,
            )
        self.item.refresh_from_db()
        self.assertFalse(enqueued)
        self.assertFalse(self.item.compensating_stop_pending)

    def test_lease_expiry_and_apply_stop_publish_completion_chaining(self):
        now = timezone.now()
        order = RecordingEditOrder.objects.create(
            review_item=self.item,
            event_detail=self.detail,
            order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
            target_video_id="stopVidA001",
            base_revision=1,
            target_revision=2,
            edl_snapshot=_valid_edl(),
            status=RecordingEditOrder.Status.QUEUED,
            active_lock_key=f"item:{self.item.pk}",
        )
        self.assertFalse(is_order_lease_expired(order, now=now))

        order.status = RecordingEditOrder.Status.RUNNING
        order.started_at = now - ORDER_LEASE_TIMEOUT + timedelta(seconds=1)
        self.assertFalse(is_order_lease_expired(order, now=now))

        order.started_at = now - ORDER_LEASE_TIMEOUT
        self.assertTrue(is_order_lease_expired(order, now=now))
        order.started_at = None
        self.assertTrue(is_order_lease_expired(order, now=now))

        mark_order_lease_expired(order, now=now)
        order.refresh_from_db()
        self.assertEqual(order.status, RecordingEditOrder.Status.FAILED)
        self.assertIsNone(order.active_lock_key)

        # apply_stop_publish_completion: target_video_id 不一致エラー
        order2 = RecordingEditOrder.objects.create(
            review_item=self.item,
            event_detail=self.detail,
            order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
            target_video_id="stopVidA001",
            base_revision=2,
            target_revision=3,
            edl_snapshot=_valid_edl(),
            status=RecordingEditOrder.Status.RUNNING,
            active_lock_key=f"item:{self.item.pk}",
            started_at=now,
        )
        with self.assertRaises(RecordingValidationError):
            apply_stop_publish_completion(
                detail=self.detail,
                item=self.item,
                order=order2,
                result_video_id="wrongVid001",
                youtube_draft_privacy="",
                review_status="",
            )

        # pending に 1本 + youtube_draft_video_id に別動画が残っている状態（untracked_current）→ True で連鎖
        self.item.pending_stop_video_ids = ["stopVidA001"]
        self.item.youtube_draft_video_id = "stopVidB002"
        self.item.review_status = RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
        self.item.youtube_draft_privacy = RecordingReviewItem.YouTubePrivacy.PRIVATE
        self.item.save(
            update_fields=[
                "pending_stop_video_ids",
                "youtube_draft_video_id",
                "review_status",
                "youtube_draft_privacy",
            ]
        )
        needs_more = apply_stop_publish_completion(
            detail=self.detail,
            item=self.item,
            order=order2,
            result_video_id="stopVidA001",
            youtube_draft_privacy="",
            review_status="",
        )
        self.item.refresh_from_db()
        self.assertTrue(needs_more)
        self.assertEqual(self.item.pending_stop_video_ids, ["stopVidB002"])
        self.assertEqual(self.item.review_status, RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED)
        self.assertEqual(self.item.youtube_draft_privacy, "")

        # 2本のうち1本を止めて pending がもう1本残っている場合の繰り上げ
        self.item.pending_stop_video_ids = ["stopVidB002", "stopVidC003"]
        self.item.youtube_draft_video_id = "stopVidB002"
        self.item.save(update_fields=["pending_stop_video_ids", "youtube_draft_video_id"])
        order2.target_video_id = "stopVidB002"
        order2.save(update_fields=["target_video_id"])
        needs_more2 = apply_stop_publish_completion(
            detail=self.detail,
            item=self.item,
            order=order2,
            result_video_id="stopVidB002",
            youtube_draft_privacy="",
            review_status="",
        )
        self.item.refresh_from_db()
        self.assertTrue(needs_more2)
        self.assertEqual(self.item.youtube_draft_video_id, "stopVidC003")
        self.assertEqual(self.item.pending_stop_video_ids, ["stopVidC003"])

        # 最後の1本を停止完了 → PRIVATE_FIXED + False
        order2.target_video_id = "stopVidC003"
        order2.save(update_fields=["target_video_id"])
        self.item.compensating_stop_pending = True
        self.item.save(update_fields=["compensating_stop_pending"])
        needs_more3 = apply_stop_publish_completion(
            detail=self.detail,
            item=self.item,
            order=order2,
            result_video_id="stopVidC003",
            youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.PRIVATE,
            review_status=RecordingReviewItem.ReviewStatus.PRIVATE_FIXED,
        )
        self.item.refresh_from_db()
        self.assertFalse(needs_more3)
        self.assertEqual(self.item.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)
        self.assertEqual(self.item.youtube_draft_privacy, RecordingReviewItem.YouTubePrivacy.PRIVATE)
        self.assertFalse(self.item.compensating_stop_pending)

    def test_record_partial_or_late_apply_edl_output_all_branches(self):
        now = timezone.now()

        # STOP_PUBLISH は no-op
        stop_order = RecordingEditOrder.objects.create(
            review_item=self.item,
            event_detail=self.detail,
            order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
            base_revision=1,
            target_revision=2,
            edl_snapshot=_valid_edl(),
            status=RecordingEditOrder.Status.FAILED,
        )
        with transaction.atomic():
            record_partial_or_late_apply_edl_output(
                detail=self.detail,
                item=self.item,
                order=stop_order,
                reported_video_id="lateVid0001",
                youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.PUBLIC,
                review_status=RecordingReviewItem.ReviewStatus.PUBLISHED,
                consent_valid=False,
                now=now,
            )
        self.assertEqual(self.item.youtube_draft_video_id, "")

        # 同意有効のまま APPLY_EDL が失敗したが、元 PRIVATE_FIXED に別動画 ID（unlisted）が遅延報告されたケース
        self.item.review_status = RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
        self.item.youtube_draft_privacy = RecordingReviewItem.YouTubePrivacy.PRIVATE
        self.item.youtube_draft_video_id = "oldVid00001"
        self.item.save(update_fields=["review_status", "youtube_draft_privacy", "youtube_draft_video_id"])
        apply_order = RecordingEditOrder.objects.create(
            review_item=self.item,
            event_detail=self.detail,
            order_type=RecordingEditOrder.OrderType.APPLY_EDL,
            base_revision=2,
            target_revision=3,
            edl_snapshot=_valid_edl(),
            status=RecordingEditOrder.Status.FAILED,
        )
        with transaction.atomic():
            record_partial_or_late_apply_edl_output(
                detail=self.detail,
                item=self.item,
                order=apply_order,
                reported_video_id="lateUnl0001",
                youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.UNLISTED,
                review_status="",
                consent_valid=True,
                now=now,
            )
        self.item.refresh_from_db()
        apply_order.refresh_from_db()
        self.assertEqual(apply_order.result_video_id, "lateUnl0001")
        self.assertEqual(self.item.youtube_draft_video_id, "lateUnl0001")
        self.assertEqual(self.item.review_status, RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED)
        self.assertEqual(self.item.pending_stop_video_ids, ["lateUnl0001"])
        self.assertFalse(
            RecordingEditOrder.objects.filter(
                review_item=self.item,
                order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
                status=RecordingEditOrder.Status.QUEUED,
            ).exists()
        )

        # プライバシー空・新動画 ID で PRIVATE_FIXED からの戻し
        self.item.review_status = RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
        self.item.youtube_draft_privacy = RecordingReviewItem.YouTubePrivacy.PRIVATE
        self.item.save(update_fields=["review_status", "youtube_draft_privacy"])
        with transaction.atomic():
            record_partial_or_late_apply_edl_output(
                detail=self.detail,
                item=self.item,
                order=apply_order,
                reported_video_id="lateNew0002",
                youtube_draft_privacy="",
                review_status="",
                consent_valid=True,
                now=now,
            )
        self.item.refresh_from_db()
        self.assertEqual(self.item.review_status, RecordingReviewItem.ReviewStatus.NEEDS_REVIEW)
        self.assertEqual(self.item.youtube_draft_privacy, "")

        # 動画 ID なし かつ PUBLIC 報告 + consent_valid=False → 既存動画保全＋補償 stop_publish 投入
        self.detail.youtube_url = "https://www.youtube.com/watch?v=lateNew0002"
        self.detail.save(update_fields=["youtube_url"])
        with transaction.atomic():
            record_partial_or_late_apply_edl_output(
                detail=self.detail,
                item=self.item,
                order=apply_order,
                reported_video_id="",
                youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.PUBLIC,
                review_status=RecordingReviewItem.ReviewStatus.PUBLISHED,
                consent_valid=False,
                now=now,
            )
        self.item.refresh_from_db()
        self.detail.refresh_from_db()
        self.assertIsNone(self.detail.youtube_url)
        self.assertEqual(self.item.review_status, RecordingReviewItem.ReviewStatus.PUBLISHED)
        self.assertTrue(
            RecordingEditOrder.objects.filter(
                review_item=self.item,
                order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
                status=RecordingEditOrder.Status.QUEUED,
            ).exists()
        )
