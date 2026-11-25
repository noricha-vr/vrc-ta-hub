"""録画編集キューワーカー（worker.py）と関連境界（locks/orders/completion/revocation）の単体テスト。"""
from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import MagicMock

from django.test import TestCase
from django.utils import timezone

from community.constants import RecordingPolicy
from event.models import Event, EventDetail, RecordingEditOrder, RecordingReviewItem
from event.services.recording_ops.completion import complete_edit_order
from event.services.recording_ops.errors import (
    MAX_REVISION,
    ORDER_LEASE_TIMEOUT,
    RecordingConflictError,
    RecordingNotFoundError,
    RecordingValidationError,
)
from event.services.recording_ops.locks import lock_event_detail, lock_event_detail_or_none
from event.services.recording_ops.orders import (
    create_edit_order,
    create_stop_publish_order_for_review_item,
)
from event.services.recording_ops.revocation import (
    revoke_recording_for_community,
    revoke_recording_for_detail,
    revoke_recording_for_event,
    revoke_recording_on_detail_delete,
)
from event.services.recording_ops.worker import _select_order_for_update, claim_next_edit_order
from tests.factories import make_community, make_event, make_event_detail, make_user


def _valid_edl() -> dict:
    return {
        "schema_version": 1,
        "safe_bounds": {"min_sec": 0.0, "max_sec": 600.0},
        "trim": {"start_sec": 10.0, "end_sec": 500.0},
        "boundary_candidates": {"start": [], "end": []},
        "cuts": [],
    }


def _create_item(detail, **overrides) -> RecordingReviewItem:
    defaults = {
        "event_detail": detail,
        "proxy_r2_key": f"recording-proxy/{detail.pk}/{uuid.uuid4().hex}.mp4",
        "proxy_duration_sec": 600.0,
        "proxy_uploaded_at": timezone.now(),
        "proxy_expires_at": timezone.now() + timedelta(days=7),
        "review_status": RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
        "edl_revision": 1,
        "edl_json": _valid_edl(),
    }
    defaults.update(overrides)
    return RecordingReviewItem.objects.create(**defaults)


def _create_order(item: RecordingReviewItem, detail, **overrides) -> RecordingEditOrder:
    defaults = {
        "review_item": item,
        "event_detail": detail,
        "order_type": RecordingEditOrder.OrderType.APPLY_EDL,
        "base_revision": item.edl_revision,
        "target_revision": item.edl_revision + 1,
        "edl_snapshot": _valid_edl(),
        "status": RecordingEditOrder.Status.QUEUED,
        "active_lock_key": f"item:{item.pk}",
    }
    defaults.update(overrides)
    if defaults["status"] in RecordingEditOrder.TERMINAL_STATUSES:
        defaults["active_lock_key"] = None
    return RecordingEditOrder.objects.create(**defaults)


class RecordingWorkerClaimAndRecoveryTest(TestCase):
    def setUp(self):
        self.staff = make_user(user_name="worker_staff", email="worker_staff@example.com", is_staff=True)
        self.community = make_community(name="ワーカーテスト集会", recording_allowed=True)
        self.event = make_event(self.community)
        self.detail = make_event_detail(
            self.event,
            status="approved",
            recording_policy=RecordingPolicy.PUBLIC,
        )

    def test_claim_rejects_invalid_order_types_and_filters_fifo(self):
        with self.assertRaises(RecordingValidationError):
            claim_next_edit_order(order_types=["invalid_type"])

        self.assertIsNone(claim_next_edit_order())

        detail2 = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item1 = _create_item(self.detail)
        item2 = _create_item(detail2, youtube_draft_video_id="stopVid0001")

        order1 = _create_order(item1, self.detail, order_type=RecordingEditOrder.OrderType.APPLY_EDL)
        order2 = _create_order(
            item2,
            detail2,
            order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
            target_video_id="stopVid0001",
        )

        # order_types 指定で古い apply_edl を飛び越えて stop_publish のみ取得
        claimed_stop = claim_next_edit_order(order_types=[RecordingEditOrder.OrderType.STOP_PUBLISH])
        self.assertIsNotNone(claimed_stop)
        assert claimed_stop is not None
        self.assertEqual(claimed_stop.pk, order2.pk)
        self.assertEqual(claimed_stop.status, RecordingEditOrder.Status.RUNNING)

        # 残る apply_edl を取得
        claimed_apply = claim_next_edit_order()
        self.assertIsNotNone(claimed_apply)
        assert claimed_apply is not None
        self.assertEqual(claimed_apply.pk, order1.pk)

    def test_claim_cancels_all_invalidation_reasons_and_enqueues_compensation(self):
        now = timezone.now()

        # 1. detail_not_found（event_detail=None で、停止対象動画あり → 補償 stop_publish 投入）
        item_no_detail = RecordingReviewItem.objects.create(
            event_detail=None,
            proxy_r2_key=f"recording-proxy/9999/{uuid.uuid4().hex}.mp4",
            review_status=RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED,
            youtube_draft_video_id="orphanVid01",
            edl_revision=1,
            edl_json=_valid_edl(),
        )
        o_no_detail = _create_order(item_no_detail, None)
        # stop_publish を作らないよう order_types=["apply_edl"] で claim してキャンセルだけ観測する
        self.assertIsNone(claim_next_edit_order(order_types=[RecordingEditOrder.OrderType.APPLY_EDL]))
        o_no_detail.refresh_from_db()
        self.assertEqual(o_no_detail.status, RecordingEditOrder.Status.CANCELED)
        self.assertIn("detail_not_found", o_no_detail.error_message)
        comp = RecordingEditOrder.objects.get(review_item=item_no_detail, status=RecordingEditOrder.Status.QUEUED)
        self.assertEqual(comp.order_type, RecordingEditOrder.OrderType.STOP_PUBLISH)
        comp.status = RecordingEditOrder.Status.SUCCEEDED
        comp.active_lock_key = None
        comp.save(update_fields=["status", "active_lock_key"])

        # 2. detail_deleted（youtube_url 付き → クリア & 補償 stop_publish）
        d_deleted = make_event_detail(
            self.event,
            status="approved",
            recording_policy=RecordingPolicy.PUBLIC,
            youtube_url="https://www.youtube.com/watch?v=delVid00001",
            deleted_at=now,
        )
        item_deleted = _create_item(d_deleted, youtube_draft_video_id="delVid00001")
        o_deleted = _create_order(item_deleted, d_deleted)
        self.assertIsNone(claim_next_edit_order(order_types=[RecordingEditOrder.OrderType.APPLY_EDL]))
        o_deleted.refresh_from_db()
        d_deleted.refresh_from_db()
        self.assertEqual(o_deleted.status, RecordingEditOrder.Status.CANCELED)
        self.assertIn("detail_deleted", o_deleted.error_message)
        self.assertIsNone(d_deleted.youtube_url)
        comp_del = RecordingEditOrder.objects.get(review_item=item_deleted, status=RecordingEditOrder.Status.QUEUED)
        self.assertEqual(comp_del.order_type, RecordingEditOrder.OrderType.STOP_PUBLISH)

        # 3. consent_revoked
        d_revoked = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.FORBIDDEN)
        item_revoked = _create_item(d_revoked, youtube_draft_video_id="revVid00001")
        o_revoked = _create_order(item_revoked, d_revoked)
        self.assertIsNone(claim_next_edit_order(order_types=[RecordingEditOrder.OrderType.APPLY_EDL]))
        o_revoked.refresh_from_db()
        self.assertIn("consent_revoked", o_revoked.error_message)
        self.assertTrue(
            RecordingEditOrder.objects.filter(
                review_item=item_revoked,
                order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
                status=RecordingEditOrder.Status.QUEUED,
            ).exists()
        )

        # 4. public_locked
        d_pub = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item_pub = _create_item(
            d_pub,
            review_status=RecordingReviewItem.ReviewStatus.PUBLISHED,
            youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.PUBLIC,
            youtube_draft_video_id="pubVid00001",
        )
        o_pub = _create_order(item_pub, d_pub)
        self.assertIsNone(claim_next_edit_order(order_types=[RecordingEditOrder.OrderType.APPLY_EDL]))
        o_pub.refresh_from_db()
        self.assertIn("public_locked", o_pub.error_message)

        # 5. proxy_expired
        d_exp = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item_exp = _create_item(d_exp, proxy_expires_at=now - timedelta(minutes=1))
        o_exp = _create_order(item_exp, d_exp)
        self.assertIsNone(claim_next_edit_order(order_types=[RecordingEditOrder.OrderType.APPLY_EDL]))
        o_exp.refresh_from_db()
        self.assertIn("proxy_expired", o_exp.error_message)

        # 6. compensating_stop_pending（未停止動画なしの場合はフラグだけクリア）
        d_comp = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item_comp = _create_item(d_comp, compensating_stop_pending=True)
        o_comp = _create_order(item_comp, d_comp)
        self.assertIsNone(claim_next_edit_order(order_types=[RecordingEditOrder.OrderType.APPLY_EDL]))
        o_comp.refresh_from_db()
        item_comp.refresh_from_db()
        self.assertIn("compensating_stop_pending", o_comp.error_message)
        self.assertFalse(item_comp.compensating_stop_pending)

    def test_expire_stale_running_orders_and_compensation_branches(self):
        now = timezone.now()

        # 1. started_at が 30 分超過 + 同意撤回済み + 未停止動画あり → failed 回収 + youtube_url クリア + 補償投入
        d1 = make_event_detail(
            self.event,
            status="approved",
            recording_policy=RecordingPolicy.FORBIDDEN,
            youtube_url="https://www.youtube.com/watch?v=expVid00001",
        )
        item1 = _create_item(d1, youtube_draft_video_id="expVid00001")
        o1 = _create_order(
            item1,
            d1,
            status=RecordingEditOrder.Status.RUNNING,
            started_at=now - ORDER_LEASE_TIMEOUT - timedelta(seconds=1),
            requested_by=self.staff,
        )

        # 2. started_at が None + compensating_stop_pending=True + 未停止動画なし → failed 回収 + フラグ解除
        d2 = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item2 = _create_item(d2, compensating_stop_pending=True)
        o2 = _create_order(
            item2,
            d2,
            status=RecordingEditOrder.Status.RUNNING,
            started_at=None,
        )

        # 3. まだ期限内の running オーダー（29分59秒）は回収されない
        d3 = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item3 = _create_item(d3)
        o3 = _create_order(
            item3,
            d3,
            status=RecordingEditOrder.Status.RUNNING,
            started_at=now - ORDER_LEASE_TIMEOUT + timedelta(seconds=10),
        )

        claimed = claim_next_edit_order()

        # _select_order_for_update の feature 分岐を mock QuerySet で確認
        mock_qs = MagicMock()
        mock_qs.select_for_update.return_value = mock_qs
        _select_order_for_update(mock_qs, use_skip_locked=True, use_select_for_update=True)
        mock_qs.select_for_update.assert_called_with(skip_locked=True)
        mock_qs.reset_mock()
        _select_order_for_update(mock_qs, use_skip_locked=False, use_select_for_update=True)
        mock_qs.select_for_update.assert_called_with()

        o1.refresh_from_db()
        o2.refresh_from_db()
        o3.refresh_from_db()
        d1.refresh_from_db()
        item2.refresh_from_db()

        self.assertEqual(o1.status, RecordingEditOrder.Status.FAILED)
        self.assertIsNone(o1.active_lock_key)
        self.assertIsNone(d1.youtube_url)
        self.assertEqual(o2.status, RecordingEditOrder.Status.FAILED)
        self.assertFalse(item2.compensating_stop_pending)
        self.assertEqual(o3.status, RecordingEditOrder.Status.RUNNING)

        # 投入された補償 stop_publish が同じ claim 呼び出しで即リース取得される
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed.review_item_id, item1.pk)
        self.assertEqual(claimed.order_type, RecordingEditOrder.OrderType.STOP_PUBLISH)
        self.assertEqual(claimed.requested_by_id, self.staff.pk)

    def test_locks_orders_completion_and_revocation_edge_branches(self):
        # locks
        with self.assertRaises(RecordingNotFoundError):
            lock_event_detail(9999999)
        self.assertIsNone(lock_event_detail_or_none(9999999))

        # revocation empty/no-op branches
        self.assertFalse(revoke_recording_for_detail(9999999))
        self.assertFalse(revoke_recording_for_detail(self.detail.pk))  # consent valid
        self.assertFalse(revoke_recording_on_detail_delete(9999999))
        self.assertEqual(revoke_recording_for_community(self.community.pk), 0)  # recording_allowed=True
        self.assertEqual(revoke_recording_for_event(9999999), 0)

        # event 所属集会を非許可集会に変更したときの revoke_recording_for_event（signal 迂回更新）
        item = _create_item(self.detail, youtube_draft_video_id="evtRev00001")
        disallowed_comm = make_community(name="非許可集会", recording_allowed=False)
        Event.objects.filter(pk=self.event.pk).update(community=disallowed_comm)
        self.assertEqual(revoke_recording_for_event(self.event.pk), 1)
        self.assertTrue(
            RecordingEditOrder.objects.filter(
                review_item=item,
                order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
                status=RecordingEditOrder.Status.QUEUED,
            ).exists()
        )
        # queued stop_publish を完了させて後続テストへ進む
        stop_order = RecordingEditOrder.objects.get(
            review_item=item,
            order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
            status=RecordingEditOrder.Status.QUEUED,
        )
        stop_order.status = RecordingEditOrder.Status.RUNNING
        stop_order.started_at = timezone.now()
        stop_order.save(update_fields=["status", "started_at"])
        complete_edit_order(
            stop_order.pk,
            target_revision=stop_order.target_revision,
            status=RecordingEditOrder.Status.SUCCEEDED,
        )

        # 物理削除後の item に対する create_stop_publish_order_for_review_item
        with self.assertRaises(RecordingNotFoundError):
            create_stop_publish_order_for_review_item(9999999, expected_revision=1)
        # event_detail 切り離し後・停止対象動画なし → nothing_to_stop
        item_nothing = RecordingReviewItem.objects.create(
            event_detail=None,
            review_status=RecordingReviewItem.ReviewStatus.PRIVATE_FIXED,
            youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.PRIVATE,
            youtube_draft_video_id="",
            edl_revision=1,
        )
        with self.assertRaises(RecordingConflictError) as ctx:
            create_stop_publish_order_for_review_item(item_nothing.pk, expected_revision=1)
        self.assertEqual(ctx.exception.code, "nothing_to_stop")

        # 許可戻し＋別動画で create_edit_order エッジケース検証
        self.event.community = self.community
        self.event.save(update_fields=["community"])
        d_ok = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item_ok = _create_item(d_ok)
        with self.assertRaises(RecordingValidationError):
            create_edit_order(d_ok.pk, expected_revision=1, order_type="bad")
        with self.assertRaises(RecordingValidationError):
            create_edit_order(d_ok.pk, expected_revision=1, edl_json={"schema_version": 2})

        item_ok.edl_revision = MAX_REVISION
        item_ok.save(update_fields=["edl_revision"])
        with self.assertRaises(RecordingConflictError) as ctx:
            create_edit_order(d_ok.pk, expected_revision=MAX_REVISION)
        self.assertEqual(ctx.exception.code, "revision_limit_exceeded")

        # complete_edit_order エッジケース
        item_ok.edl_revision = 1
        item_ok.save(update_fields=["edl_revision"])
        order_ok = create_edit_order(d_ok.pk, expected_revision=1, requested_by=self.staff)
        claimed = claim_next_edit_order()
        assert claimed is not None and claimed.pk == order_ok.pk

        with self.assertRaises(RecordingValidationError):
            complete_edit_order(
                order_ok.pk,
                target_revision=order_ok.target_revision,
                status=RecordingEditOrder.Status.SUCCEEDED,
                youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                result_video_id="otherVid001",
            )
        with self.assertRaises(RecordingValidationError):
            complete_edit_order(
                order_ok.pk,
                target_revision=order_ok.target_revision,
                status=RecordingEditOrder.Status.SUCCEEDED,
                youtube_draft_privacy="invalid_priv",
            )
        with self.assertRaises(RecordingValidationError):
            complete_edit_order(
                order_ok.pk,
                target_revision=order_ok.target_revision,
                status=RecordingEditOrder.Status.SUCCEEDED,
                review_status="invalid_status",
            )
        with self.assertRaises(RecordingValidationError):
            complete_edit_order(
                order_ok.pk,
                target_revision=order_ok.target_revision,
                status=RecordingEditOrder.Status.SUCCEEDED,
                error_message=123,  # type: ignore[arg-type]
            )
        with self.assertRaises(RecordingValidationError):
            complete_edit_order(
                order_ok.pk,
                target_revision=order_ok.target_revision,
                status=RecordingEditOrder.Status.SUCCEEDED,
                youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.PUBLIC,
                review_status=RecordingReviewItem.ReviewStatus.PUBLISHED,
            )

        # 未公開（unlisted）完了時に youtube_url を渡しても detail.youtube_url へは反映せず警告ログを出す
        with self.assertLogs("event.services.recording_ops.completion", level="WARNING") as logs:
            completed = complete_edit_order(
                order_ok.pk,
                target_revision=order_ok.target_revision,
                status=RecordingEditOrder.Status.SUCCEEDED,
                youtube_url="https://youtu.be/dQw4w9WgXcQ",
                youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.UNLISTED,
            )
        self.assertEqual(completed.result_video_id, "dQw4w9WgXcQ")
        d_ok.refresh_from_db()
        self.assertIsNone(d_ok.youtube_url)
        self.assertIn("recording_order_youtube_url_skipped", "\n".join(logs.output))

    def test_unapproved_detail_cancels_apply_edl_claim_and_preserves_stop_publish(self):
        for unapproved_status in ("pending", "rejected"):
            with self.subTest(status=unapproved_status):
                detail = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
                item = _create_item(
                    detail,
                    youtube_draft_video_id="unappVid001",
                    youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.UNLISTED,
                    review_status=RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED,
                )
                apply_order = RecordingEditOrder.objects.create(
                    review_item=item,
                    event_detail=detail,
                    order_type=RecordingEditOrder.OrderType.APPLY_EDL,
                    base_revision=1,
                    target_revision=2,
                    edl_snapshot=_valid_edl(),
                    status=RecordingEditOrder.Status.QUEUED,
                    active_lock_key=f"item:{item.pk}",
                )
                # シグナルを経由せず直接 DB が未承認になったケースでも claim 側が apply_edl をキャンセルする
                EventDetail.objects.filter(pk=detail.pk).update(status=unapproved_status)
                claimed = claim_next_edit_order(order_types=[RecordingEditOrder.OrderType.APPLY_EDL])
                self.assertIsNone(claimed)
                apply_order.refresh_from_db()
                self.assertEqual(apply_order.status, RecordingEditOrder.Status.CANCELED)
                # 補償 stop_publish が自動投入され、stop_publish の claim は維持される
                claimed_stop = claim_next_edit_order(order_types=[RecordingEditOrder.OrderType.STOP_PUBLISH])
                self.assertIsNotNone(claimed_stop)
                assert claimed_stop is not None
                self.assertEqual(claimed_stop.review_item_id, item.pk)
                self.assertEqual(claimed_stop.order_type, RecordingEditOrder.OrderType.STOP_PUBLISH)
                claimed_stop.status = RecordingEditOrder.Status.SUCCEEDED
                claimed_stop.active_lock_key = None
                claimed_stop.save(update_fields=["status", "active_lock_key"])

    def test_unapproving_detail_via_update_fields_triggers_revocation_compensation(self):
        # 1) 公開済み（youtube_url あり）から status="pending" へ保存 → URL 消去 + 補償 stop_publish 投入
        d_pub = make_event_detail(
            self.event,
            status="approved",
            recording_policy=RecordingPolicy.PUBLIC,
            youtube_url="https://www.youtube.com/watch?v=pubUnapp001",
        )
        item_pub = _create_item(
            d_pub,
            youtube_draft_video_id="pubUnapp001",
            youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.PUBLIC,
            review_status=RecordingReviewItem.ReviewStatus.PUBLISHED,
        )
        d_pub.status = "pending"
        d_pub.save(update_fields=["status"])
        d_pub.refresh_from_db()
        self.assertIsNone(d_pub.youtube_url)
        self.assertTrue(
            RecordingEditOrder.objects.filter(
                review_item=item_pub,
                order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
                status=RecordingEditOrder.Status.QUEUED,
                target_video_id="pubUnapp001",
            ).exists()
        )

        # 2) queued apply_edl がある状態で status="rejected" へ保存 → queued 自動キャンセル + 補償 stop_publish 投入
        d_q = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item_q = _create_item(
            d_q,
            youtube_draft_video_id="qUnapp00001",
            youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.UNLISTED,
            review_status=RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED,
        )
        ord_q = RecordingEditOrder.objects.create(
            review_item=item_q,
            event_detail=d_q,
            order_type=RecordingEditOrder.OrderType.APPLY_EDL,
            base_revision=1,
            target_revision=2,
            edl_snapshot=_valid_edl(),
            status=RecordingEditOrder.Status.QUEUED,
            active_lock_key=f"item:{item_q.pk}",
        )
        d_q.status = "rejected"
        d_q.save(update_fields=["status"])
        ord_q.refresh_from_db()
        self.assertEqual(ord_q.status, RecordingEditOrder.Status.CANCELED)
        self.assertTrue(
            RecordingEditOrder.objects.filter(
                review_item=item_q,
                order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
                status=RecordingEditOrder.Status.QUEUED,
            ).exists()
        )

        # 3) running apply_edl がある状態で status="pending" へ保存 → compensating_stop_pending=True
        d_run = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item_run = _create_item(
            d_run,
            youtube_draft_video_id="runUnapp001",
            youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.UNLISTED,
            review_status=RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED,
        )
        RecordingEditOrder.objects.create(
            review_item=item_run,
            event_detail=d_run,
            order_type=RecordingEditOrder.OrderType.APPLY_EDL,
            base_revision=1,
            target_revision=2,
            edl_snapshot=_valid_edl(),
            status=RecordingEditOrder.Status.RUNNING,
            started_at=timezone.now(),
            active_lock_key=f"item:{item_run.pk}",
        )
        d_run.status = "pending"
        d_run.save(update_fields=["status"])
        item_run.refresh_from_db()
        self.assertTrue(item_run.compensating_stop_pending)
