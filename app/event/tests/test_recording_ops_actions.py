"""録画編集キューの再試行・取り消し・期限切れ表示・緊急停止操作（#956）のテスト。"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

from bs4 import BeautifulSoup
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from community.constants import RecordingPolicy
from community.models import CommunityMember
from event.models import RecordingEditOrder, RecordingReviewItem
from event.services.recording_ops.completion import complete_edit_order
from event.services.recording_ops.errors import ORDER_LEASE_TIMEOUT, RecordingConflictError
from event.services.recording_ops.order_actions import cancel_edit_order
from event.services.recording_ops.queries import (
    build_recording_detail_context,
    build_recording_list_queryset,
    get_recording_list_summary,
)
from event.services.recording_ops.storage import (
    RecordingProxyExpiredError,
    create_presigned_proxy_download_url,
)
from tests.factories import (
    make_community,
    make_community_member,
    make_discord_linked_user,
    make_event,
    make_event_detail,
)


def _r2_settings():
    return override_settings(
        AWS_ACCESS_KEY_ID="test-r2-key",
        AWS_SECRET_ACCESS_KEY="test-r2-secret",
        AWS_STORAGE_BUCKET_NAME="vrc-ta-hub-public",
        AWS_PRIVATE_STORAGE_BUCKET_NAME="vrc-ta-hub-private",
        AWS_S3_ENDPOINT_URL="https://r2.example.invalid",
    )


def _valid_edl(*, start_sec: float = 20.0, end_sec: float = 800.0, max_sec: float = 1200.0) -> dict:
    return {
        "schema_version": 1,
        "safe_bounds": {"min_sec": 0.0, "max_sec": max_sec},
        "trim": {"start_sec": start_sec, "end_sec": end_sec},
        "boundary_candidates": {
            "start": [{"sec": 20.0, "label": "挨拶開始"}],
            "end": [{"sec": 800.0, "label": "質疑終了"}],
        },
        "cuts": [],
    }


def _proxy_key(detail_id: int) -> str:
    return f"recording-proxy/{detail_id}/{uuid.uuid4().hex}.mp4"


@_r2_settings()
class RecordingOpsActionsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = make_discord_linked_user(
            user_name="ops_staff", email="ops_staff@example.com", is_staff=True
        )
        cls.owner = make_discord_linked_user(user_name="ops_owner", email="ops_owner@example.com")
        cls.community_staff = make_discord_linked_user(
            user_name="ops_cstaff", email="ops_cstaff@example.com"
        )
        cls.regular_user = make_discord_linked_user(
            user_name="ops_user", email="ops_user@example.com"
        )
        cls.community = make_community(name="運用アクション集会", owner=cls.owner, recording_allowed=True)
        make_community_member(cls.community, cls.community_staff, CommunityMember.Role.STAFF)
        cls.event = make_event(cls.community)

        cls.detail = make_event_detail(
            cls.event,
            status="approved",
            speaker="登壇者Ops",
            theme="失敗再試行と緊急停止テスト発表",
            recording_policy=RecordingPolicy.PUBLIC,
        )
        cls.forbidden_detail = make_event_detail(
            cls.event,
            status="approved",
            speaker="登壇者Forbidden",
            theme="撮影禁止発表",
            recording_policy=RecordingPolicy.FORBIDDEN,
        )

    def setUp(self):
        cache.clear()
        now = timezone.now()
        self.item = RecordingReviewItem.objects.create(
            event_detail=self.detail,
            proxy_r2_key=_proxy_key(self.detail.pk),
            proxy_duration_sec=1200.0,
            proxy_uploaded_at=now - timedelta(days=1),
            proxy_expires_at=now + timedelta(days=29),
            review_status=RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
            edl_revision=1,
            edl_json=_valid_edl(),
        )

    def _list_url(self) -> str:
        return reverse("event:recording_review_list")

    def _detail_url(self, detail_id: int | None = None) -> str:
        return reverse("event:recording_review_detail", kwargs={"detail_id": detail_id or self.detail.pk})

    def _retry_url(self, order_id: int, detail_id: int | None = None) -> str:
        return reverse(
            "event:recording_order_retry",
            kwargs={"detail_id": detail_id or self.detail.pk, "order_id": order_id},
        )

    def _cancel_url(self, order_id: int, detail_id: int | None = None) -> str:
        return reverse(
            "event:recording_order_cancel",
            kwargs={"detail_id": detail_id or self.detail.pk, "order_id": order_id},
        )

    def _stop_url(self, detail_id: int | None = None) -> str:
        return reverse("event:recording_stop_publish", kwargs={"detail_id": detail_id or self.detail.pk})

    def test_permissions_block_anonymous_and_non_staff_on_all_action_endpoints(self):
        order = RecordingEditOrder.objects.create(
            review_item=self.item,
            event_detail=self.detail,
            order_type=RecordingEditOrder.OrderType.APPLY_EDL,
            base_revision=1,
            target_revision=2,
            edl_snapshot=_valid_edl(),
            status=RecordingEditOrder.Status.FAILED,
            active_lock_key=None,
        )
        urls = (
            self._retry_url(order.pk),
            self._cancel_url(order.pk),
            self._stop_url(),
        )
        self.client.logout()
        for url in urls:
            resp = self.client.post(url, {"expected_revision": "1"})
            self.assertEqual(resp.status_code, 302)
            self.assertTrue(resp["Location"].startswith("/account/login/"))

        for non_staff in (self.regular_user, self.owner, self.community_staff):
            self.client.force_login(non_staff)
            for url in urls:
                with self.subTest(user=non_staff.user_name, url=url):
                    self.assertEqual(self.client.post(url, {"expected_revision": "1"}).status_code, 403)
                    self.assertEqual(
                        self.client.post(
                            url,
                            data=json.dumps({"expected_revision": 1}),
                            content_type="application/json",
                        ).status_code,
                        403,
                    )

        # スタッフでも同意なし発表への操作は 403
        self.client.force_login(self.staff)
        self.assertEqual(
            self.client.post(
                self._stop_url(self.forbidden_detail.pk),
                data=json.dumps({"expected_revision": 1}),
                content_type="application/json",
            ).status_code,
            403,
        )

    @patch("event.services.recording_ops.storage.boto3.client")
    def test_expired_and_deleted_proxy_denies_presigned_url_and_blocks_apply_edl_and_retry(self, mock_boto):
        # R2 30日削除後（proxy_r2_key が空 + EXPIRED）でも署名 URL は発給されない
        self.item.proxy_r2_key = ""
        self.item.proxy_expires_at = timezone.now() - timedelta(days=1)
        self.item.review_status = RecordingReviewItem.ReviewStatus.EXPIRED
        self.item.edl_revision = 2
        self.item.save(update_fields=["proxy_r2_key", "proxy_expires_at", "review_status", "edl_revision"])

        with self.assertRaises(RecordingProxyExpiredError):
            create_presigned_proxy_download_url(self.detail)
        mock_boto.assert_not_called()

        failed_apply = RecordingEditOrder.objects.create(
            review_item=self.item,
            event_detail=self.detail,
            order_type=RecordingEditOrder.OrderType.APPLY_EDL,
            base_revision=1,
            target_revision=2,
            edl_snapshot=_valid_edl(),
            status=RecordingEditOrder.Status.FAILED,
            error_message="encode timeout",
            active_lock_key=None,
        )

        self.client.force_login(self.staff)
        detail_resp = self.client.get(self._detail_url())
        self.assertEqual(detail_resp.status_code, 200)
        self.assertContains(detail_resp, 'data-proxy-state="expired"')
        self.assertContains(detail_resp, "視聴期限（公開後 30 日）経過のためプロキシ動画は削除されました")
        soup = BeautifulSoup(detail_resp.content, "html.parser")
        self.assertIsNone(soup.select_one("video#recording-proxy-player"))
        self.assertTrue(soup.select_one("#recording-submit-btn").has_attr("disabled"))
        self.assertIsNone(soup.select_one('[data-testid="retry-failed-order-btn"]'))
        self.assertIsNotNone(soup.select_one('[data-testid="retry-blocked-expired-note"]'))
        self.assertIsNotNone(soup.select_one('[data-testid="cancel-failed-order-trigger"]'))
        mock_boto.assert_not_called()

        # 一覧フィルタでも expired に含まれ、unrecorded には入らない
        self.assertEqual(build_recording_list_queryset(status_filter="expired").filter(pk=self.detail.pk).count(), 1)
        self.assertEqual(build_recording_list_queryset(status_filter="unrecorded").filter(pk=self.detail.pk).count(), 0)

        # 再描画 (apply_edl) と 失敗 apply_edl の再試行はいずれも 409 proxy_expired で抑止
        submit_resp = self.client.post(
            self._detail_url(),
            data=json.dumps({"expected_revision": 2, "start_sec": 20.0, "end_sec": 800.0}),
            content_type="application/json",
        )
        self.assertEqual(submit_resp.status_code, 409)
        self.assertEqual(submit_resp.json()["code"], "proxy_expired")

        retry_resp = self.client.post(
            self._retry_url(failed_apply.pk),
            data=json.dumps({"expected_revision": 2}),
            content_type="application/json",
        )
        self.assertEqual(retry_resp.status_code, 409)
        self.assertEqual(retry_resp.json()["code"], "proxy_expired")

        # 失敗オーダーの「取り消し」は期限切れでも実行可能
        cancel_resp = self.client.post(
            self._cancel_url(failed_apply.pk),
            data=json.dumps({"expected_revision": 2}),
            content_type="application/json",
        )
        self.assertEqual(cancel_resp.status_code, 200)
        self.assertEqual(cancel_resp.json()["order_status"], RecordingEditOrder.Status.CANCELED)
        self.assertEqual(cancel_resp.json()["edl_revision"], 3)

    @patch("event.services.recording_ops.storage.boto3.client")
    def test_list_action_required_aggregates_failed_and_stalled_orders_and_supports_retry_cancel(self, mock_boto):
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://r2.example.invalid/signed?sig=ok"
        mock_boto.return_value = mock_s3

        now = timezone.now()
        # 1) failed オーダー付き発表（self.detail）
        self.item.edl_revision = 2
        self.item.save(update_fields=["edl_revision"])
        failed_order = RecordingEditOrder.objects.create(
            review_item=self.item,
            event_detail=self.detail,
            order_type=RecordingEditOrder.OrderType.APPLY_EDL,
            base_revision=1,
            target_revision=2,
            edl_snapshot=_valid_edl(start_sec=25.0, end_sec=750.0),
            status=RecordingEditOrder.Status.FAILED,
            error_message="ffmpeg exited with status 1",
            active_lock_key=None,
        )

        # 2) queued のまま 45 分滞留している発表
        stalled_queued_detail = make_event_detail(
            self.event, status="approved", speaker="登壇者StalledQ", theme="滞留queued発表",
            recording_policy=RecordingPolicy.PUBLIC,
        )
        stalled_q_item = RecordingReviewItem.objects.create(
            event_detail=stalled_queued_detail,
            proxy_r2_key=_proxy_key(stalled_queued_detail.pk),
            proxy_duration_sec=900.0,
            proxy_uploaded_at=now,
            proxy_expires_at=now + timedelta(days=10),
            review_status=RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
            edl_revision=2,
            edl_json=_valid_edl(max_sec=900.0),
        )
        stalled_q_order = RecordingEditOrder.objects.create(
            review_item=stalled_q_item,
            event_detail=stalled_queued_detail,
            order_type=RecordingEditOrder.OrderType.APPLY_EDL,
            base_revision=1,
            target_revision=2,
            edl_snapshot=_valid_edl(max_sec=900.0),
            status=RecordingEditOrder.Status.QUEUED,
            active_lock_key=f"item:{stalled_q_item.pk}",
        )
        RecordingEditOrder.objects.filter(pk=stalled_q_order.pk).update(
            created_at=now - timedelta(minutes=45)
        )

        # 3) running のまま 35 分滞留している発表
        stalled_run_detail = make_event_detail(
            self.event, status="approved", speaker="登壇者StalledR", theme="滞留running発表",
            recording_policy=RecordingPolicy.PUBLIC,
        )
        stalled_r_item = RecordingReviewItem.objects.create(
            event_detail=stalled_run_detail,
            proxy_r2_key=_proxy_key(stalled_run_detail.pk),
            proxy_duration_sec=900.0,
            proxy_uploaded_at=now,
            proxy_expires_at=now + timedelta(days=10),
            review_status=RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
            edl_revision=2,
            edl_json=_valid_edl(max_sec=900.0),
        )
        stalled_r_order = RecordingEditOrder.objects.create(
            review_item=stalled_r_item,
            event_detail=stalled_run_detail,
            order_type=RecordingEditOrder.OrderType.APPLY_EDL,
            base_revision=1,
            target_revision=2,
            edl_snapshot=_valid_edl(max_sec=900.0),
            status=RecordingEditOrder.Status.RUNNING,
            started_at=now - ORDER_LEASE_TIMEOUT - timedelta(minutes=5),
            active_lock_key=f"item:{stalled_r_item.pk}",
        )

        summary = get_recording_list_summary(now=now)
        self.assertEqual(summary.failed_order_count, 1)
        self.assertEqual(summary.stalled_order_count, 2)
        self.assertEqual(summary.action_required_count, 3)

        stalled_qs = build_recording_list_queryset(order_filter="stalled", now=now)
        self.assertEqual(
            set(stalled_qs.values_list("pk", flat=True)),
            {stalled_queued_detail.pk, stalled_run_detail.pk},
        )

        self.client.force_login(self.staff)
        list_resp = self.client.get(self._list_url())
        self.assertEqual(list_resp.status_code, 200)
        soup = BeautifulSoup(list_resp.content, "html.parser")
        action_box = soup.select_one('[data-testid="recording-action-required"]')
        self.assertIsNotNone(action_box)
        self.assertIn("要対応 3", action_box.get_text())
        self.assertIsNotNone(action_box.select_one('[data-testid="filter-failed-orders-btn"]'))
        self.assertIsNotNone(action_box.select_one('[data-testid="filter-stalled-orders-btn"]'))
        self.assertIsNotNone(action_box.select_one(f'[data-testid="list-retry-order-btn-{self.detail.pk}"]'))
        self.assertIsNotNone(action_box.select_one(f'[data-testid="list-cancel-order-trigger-{self.detail.pk}"]'))
        self.assertIsNotNone(action_box.select_one(f'[data-testid="list-cancel-stalled-trigger-{stalled_queued_detail.pk}"]'))
        # running の滞留オーダーは取り消しトリガを出さない
        self.assertIsNone(action_box.select_one(f'[data-testid="list-cancel-stalled-trigger-{stalled_run_detail.pk}"]'))

        # running 中オーダーへの取り消し・再試行 POST は 409 Conflict
        cancel_running = self.client.post(
            self._cancel_url(stalled_r_order.pk, detail_id=stalled_run_detail.pk),
            data=json.dumps({"expected_revision": 2}),
            content_type="application/json",
        )
        self.assertEqual(cancel_running.status_code, 409)
        self.assertEqual(cancel_running.json()["code"], "order_running")

        retry_running = self.client.post(
            self._retry_url(stalled_r_order.pk, detail_id=stalled_run_detail.pk),
            data=json.dumps({"expected_revision": 2}),
            content_type="application/json",
        )
        self.assertEqual(retry_running.status_code, 409)
        self.assertEqual(retry_running.json()["code"], "order_running")

        # 一覧から failed オーダーを Form POST (next_view=list) で再試行 → 302 一覧へリダイレクト
        with self.assertLogs("event.services.recording_ops.order_actions", level="INFO") as logs:
            retry_ok = self.client.post(
                self._retry_url(failed_order.pk),
                {"expected_revision": "2", "next_view": "list"},
            )
        self.assertEqual(retry_ok.status_code, 302)
        self.assertEqual(retry_ok["Location"], self._list_url())
        self.assertIn("recording_order_retried", "\n".join(logs.output))

        self.item.refresh_from_db()
        self.assertEqual(self.item.edl_revision, 3)
        retried_order = RecordingEditOrder.objects.filter(review_item=self.item).order_by("-pk").first()
        assert retried_order is not None
        self.assertEqual(retried_order.status, RecordingEditOrder.Status.QUEUED)
        self.assertEqual(retried_order.edl_snapshot["trim"], {"start_sec": 25.0, "end_sec": 750.0})

        # 古い failed_order を古いリビジョンで再試行しても 409 Conflict
        stale_retry = self.client.post(
            self._retry_url(failed_order.pk),
            {"expected_revision": "2"},
        )
        self.assertEqual(stale_retry.status_code, 409)

        # queued オーダーの取り消し（Form POST next_view=list）
        cancel_queued = self.client.post(
            self._cancel_url(stalled_q_order.pk, detail_id=stalled_queued_detail.pk),
            {"expected_revision": "2", "next_view": "list"},
        )
        self.assertEqual(cancel_queued.status_code, 302)
        stalled_q_order.refresh_from_db()
        self.assertEqual(stalled_q_order.status, RecordingEditOrder.Status.CANCELED)
        self.assertIsNone(stalled_q_order.active_lock_key)

    @patch("event.services.recording_ops.storage.boto3.client")
    def test_public_locked_allows_emergency_stop_via_confirm_panel_only(self, mock_boto):
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://r2.example.invalid/signed?sig=pub"
        mock_boto.return_value = mock_s3

        self.item.review_status = RecordingReviewItem.ReviewStatus.PUBLISHED
        self.item.youtube_draft_privacy = RecordingReviewItem.YouTubePrivacy.PUBLIC
        self.item.youtube_draft_video_id = "pubVid00001"
        self.item.edl_revision = 4
        self.item.save(
            update_fields=[
                "review_status",
                "youtube_draft_privacy",
                "youtube_draft_video_id",
                "edl_revision",
            ]
        )
        self.detail.youtube_url = "https://www.youtube.com/watch?v=pubVid00001"
        self.detail.save(update_fields=["youtube_url"])

        self.client.force_login(self.staff)
        detail_resp = self.client.get(self._detail_url())
        self.assertEqual(detail_resp.status_code, 200)
        soup = BeautifulSoup(detail_resp.content, "html.parser")
        self.assertIsNotNone(soup.select_one('[data-testid="public-locked-banner"]'))
        self.assertTrue(soup.select_one("#recording-submit-btn").has_attr("disabled"))

        # confirm_panel.html 経由の緊急停止パネルが用意されていること
        stop_trigger = soup.select_one('[data-testid="emergency-stop-trigger"]')
        stop_panel = soup.select_one("#recording-stop-publish-panel")
        self.assertIsNotNone(stop_trigger)
        self.assertIsNotNone(stop_panel)
        self.assertEqual(stop_trigger["data-confirm-panel"], "recording-stop-publish-panel")
        stop_form = stop_panel.select_one("form")
        self.assertEqual(stop_form["action"], self._stop_url())
        self.assertEqual(stop_form.select_one('input[name="expected_revision"]')["value"], "4")

        # 通常の再編集 apply_edl は 409 public_locked
        blocked_apply = self.client.post(
            self._detail_url(),
            data=json.dumps({"expected_revision": 4, "start_sec": 20.0, "end_sec": 800.0}),
            content_type="application/json",
        )
        self.assertEqual(blocked_apply.status_code, 409)
        self.assertEqual(blocked_apply.json()["code"], "public_locked")

        # expected_revision 不一致での緊急停止は 409 revision_conflict
        stale_stop = self.client.post(
            self._stop_url(),
            {"expected_revision": "3"},
        )
        self.assertEqual(stale_stop.status_code, 409)
        stale_soup = BeautifulSoup(stale_stop.content, "html.parser")
        self.assertFalse(stale_soup.select_one("#recording-conflict-banner").has_attr("hidden"))

        # 正しい expected_revision で緊急停止を起票 → 302 & stop_publish キュー作成
        ok_stop = self.client.post(
            self._stop_url(),
            {"expected_revision": "4"},
        )
        self.assertEqual(ok_stop.status_code, 302)
        self.assertEqual(ok_stop["Location"], self._detail_url())

        self.item.refresh_from_db()
        self.assertEqual(self.item.edl_revision, 5)
        self.assertTrue(self.item.is_public_locked)
        stop_order = RecordingEditOrder.objects.get(review_item=self.item)
        self.assertEqual(stop_order.order_type, RecordingEditOrder.OrderType.STOP_PUBLISH)
        self.assertEqual(stop_order.target_video_id, "pubVid00001")
        self.assertEqual(stop_order.status, RecordingEditOrder.Status.QUEUED)

        # 処理中は二重送信を 409 active_order_exists で遮断
        dup_stop = self.client.post(
            self._stop_url(),
            data=json.dumps({"expected_revision": 5}),
            content_type="application/json",
        )
        self.assertEqual(dup_stop.status_code, 409)
        self.assertEqual(dup_stop.json()["code"], "active_order_exists")

        # stop_publish 完了（private_fixed）後は停止対象なし 409 nothing_to_stop、UI も完了表示
        stop_order.status = RecordingEditOrder.Status.RUNNING
        stop_order.started_at = timezone.now()
        stop_order.save(update_fields=["status", "started_at"])
        complete_edit_order(
            stop_order.pk,
            target_revision=5,
            status=RecordingEditOrder.Status.SUCCEEDED,
        )

        self.item.refresh_from_db()
        self.assertEqual(self.item.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)
        self.assertTrue(self.item.is_public_locked)

        nothing_stop = self.client.post(
            self._stop_url(),
            data=json.dumps({"expected_revision": 5}),
            content_type="application/json",
        )
        self.assertEqual(nothing_stop.status_code, 409)
        self.assertEqual(nothing_stop.json()["code"], "nothing_to_stop")

        after_resp = self.client.get(self._detail_url())
        after_soup = BeautifulSoup(after_resp.content, "html.parser")
        self.assertIsNotNone(after_soup.select_one('[data-testid="emergency-stop-completed-note"]'))
        self.assertIsNone(after_soup.select_one('[data-testid="emergency-stop-trigger"]'))

    def test_detail_context_reports_stalled_active_order(self):
        now = timezone.now()
        order = RecordingEditOrder.objects.create(
            review_item=self.item,
            event_detail=self.detail,
            order_type=RecordingEditOrder.OrderType.APPLY_EDL,
            base_revision=1,
            target_revision=2,
            edl_snapshot=_valid_edl(),
            status=RecordingEditOrder.Status.RUNNING,
            started_at=now - timedelta(minutes=42),
            active_lock_key=f"item:{self.item.pk}",
        )
        ctx = build_recording_detail_context(self.detail.pk, issue_presigned_url=False, now=now)
        self.assertEqual(ctx.active_order, order)
        self.assertTrue(ctx.stalled_active_order)
        self.assertEqual(ctx.stalled_minutes, 42)
        self.assertFalse(ctx.can_cancel_latest_order)

    def test_canceled_failed_order_still_preserves_late_worker_video_for_compensation(self):
        self.item.edl_revision = 2
        self.item.save(update_fields=["edl_revision"])
        lease_failed = RecordingEditOrder.objects.create(
            review_item=self.item,
            event_detail=self.detail,
            order_type=RecordingEditOrder.OrderType.APPLY_EDL,
            base_revision=1,
            target_revision=2,
            edl_snapshot=_valid_edl(),
            status=RecordingEditOrder.Status.FAILED,
            started_at=timezone.now() - timedelta(minutes=35),
            error_message="lease expired",
            active_lock_key=None,
        )
        canceled = cancel_edit_order(
            self.detail.pk, lease_failed.pk, expected_revision=2, requested_by=self.staff
        )
        self.assertEqual(canceled.status, RecordingEditOrder.Status.CANCELED)

        # 取り消し後に旧ワーカーが公開動画を遅延報告した場合も 409 を返しつつ動画 ID を保全し、
        # 同意撤回後であれば補償 stop_publish を自動投入すること
        self.detail.recording_policy = RecordingPolicy.FORBIDDEN
        self.detail.save(update_fields=["recording_policy"])
        with self.assertRaises(RecordingConflictError) as ctx:
            complete_edit_order(
                lease_failed.pk,
                target_revision=2,
                status=RecordingEditOrder.Status.SUCCEEDED,
                result_video_id="lateCancVd1",
                youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.PUBLIC,
            )
        self.assertEqual(ctx.exception.code, "order_not_running")
        self.item.refresh_from_db()
        self.assertEqual(self.item.youtube_draft_video_id, "lateCancVd1")
        self.assertIn("lateCancVd1", self.item.pending_stop_video_ids)
        self.assertTrue(
            RecordingEditOrder.objects.filter(
                review_item=self.item,
                order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
                status=RecordingEditOrder.Status.QUEUED,
                target_video_id="lateCancVd1",
            ).exists()
        )

    def test_short_remaining_proxy_shows_expiring_soon_heading_instead_of_deleted(self):
        self.item.proxy_expires_at = timezone.now() + timedelta(minutes=10)
        self.item.save(update_fields=["proxy_expires_at"])

        self.client.force_login(self.staff)
        resp = self.client.get(self._detail_url())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "プロキシ動画の視聴期限が間近のため視聴できません")
        self.assertNotContains(resp, "削除されました")
