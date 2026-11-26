"""worker_synced_revision と proxy-url 安全フラグ（#977）のテスト。"""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from event.models import EventDetail, RecordingEditOrder, RecordingReviewItem
from event.services.recording_ops.completion import complete_edit_order
from event.services.recording_ops.items import upsert_recording_item
from event.services.recording_ops.order_actions import cancel_edit_order
from event.services.recording_ops.orders import create_edit_order
from tests.factories import make_community, make_event, make_event_detail, make_user
from user_account.models import APIKey


def _edl(start: float = 0.0, end: float = 60.0) -> dict:
    return {
        "schema_version": 1,
        "safe_bounds": {"min_sec": 0.0, "max_sec": 120.0},
        "trim": {"start_sec": start, "end_sec": end},
        "cuts": [],
        "boundary_candidates": {"start": [], "end": []},
    }


class WorkerSyncedRevisionTest(TestCase):
    def setUp(self):
        self.superuser = make_user(
            user_name="wsr_super", email="wsr_super@example.com", is_staff=True, is_superuser=True
        )
        _, self.super_key = APIKey.create_with_raw_key(user=self.superuser, name="wsr-super")
        self.community = make_community(name="同期テスト集会", status="approved", recording_allowed=True)
        self.event = make_event(self.community, event_date=date(2026, 11, 26))
        self.detail = make_event_detail(
            self.event,
            detail_type="LT",
            status="approved",
            speaker="登壇者",
            theme="テーマ",
            recording_policy=EventDetail.RecordingPolicy.PUBLIC,
        )

    def _client(self) -> APIClient:
        client = APIClient()
        client.credentials(HTTP_X_API_KEY=self.super_key)
        return client

    def _proxy_url(self, item: RecordingReviewItem) -> dict:
        fake_res = SimpleNamespace(
            review_item=item,
            proxy_url="https://example.r2.cloudflarestorage.com/proxy.mp4?sig=1",
            expires_in=1200,
            expires_at=timezone.now() + timedelta(seconds=1200),
        )
        with mock.patch("api_v1.recording_ops_views.create_presigned_proxy_download_url", return_value=fake_res):
            resp = self._client().get(reverse("recording-ops-proxy-url", kwargs={"detail_id": self.detail.pk}))
        self.assertEqual(resp.status_code, 200, resp.data)
        return resp.json()

    def test_worker_synced_revision_tracks_upsert_apply_cancel_and_stop(self):
        # 1. 初回作成 -> edl_revision=1, worker_synced_revision=1
        item, created = upsert_recording_item(
            self.detail.pk,
            proxy_r2_key=f"recording-proxy/{self.detail.pk}/0123456789abcdef0123456789abcdef.mp4",
            proxy_duration_sec=120.0,
            edl_json=_edl(),
            youtube_draft_video_id="vid12345678",
            youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.UNLISTED,
        )
        self.assertTrue(created)
        self.assertEqual(item.edl_revision, 1)
        self.assertEqual(item.worker_synced_revision, 1)

        # 2. PUT 登録更新 -> 両方 2 に進む
        item, _ = upsert_recording_item(
            self.detail.pk,
            youtube_draft_video_id="vid12345678",
            youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.UNLISTED,
            expected_revision=1,
        )
        self.assertEqual(item.edl_revision, 2)
        self.assertEqual(item.worker_synced_revision, 2)

        # 3. オーダー作成 → スタッフ取消 -> edl_revision=4 に進むが worker_synced_revision は 2 のまま！
        order = create_edit_order(
            self.detail.pk,
            order_type=RecordingEditOrder.OrderType.APPLY_EDL,
            expected_revision=2,
            edl_json=_edl(start=5.0, end=60.0),
            requested_by=self.superuser,
        )
        cancel_edit_order(self.detail.pk, order.pk, expected_revision=3, requested_by=self.superuser)
        item.refresh_from_db()
        self.assertEqual(item.edl_revision, 4)
        self.assertEqual(item.worker_synced_revision, 2)

        data = self._proxy_url(item)
        self.assertEqual(data["edl_revision"], 4)
        self.assertEqual(data["worker_synced_revision"], 2)
        self.assertFalse(data["has_latest_order_failed"])
        self.assertFalse(data["has_pending_stop_videos"])
        self.assertFalse(data["compensating_stop_pending"])

        # 4. オーダー失敗 -> has_latest_order_failed=True, worker_synced_revision は据え置き
        order2 = create_edit_order(
            self.detail.pk,
            order_type=RecordingEditOrder.OrderType.APPLY_EDL,
            expected_revision=4,
            edl_json=_edl(start=10.0, end=60.0),
            requested_by=self.superuser,
        )
        order2.status = RecordingEditOrder.Status.RUNNING
        order2.started_at = timezone.now()
        order2.save(update_fields=["status", "started_at"])
        complete_edit_order(
            order2.pk,
            target_revision=5,
            status=RecordingEditOrder.Status.FAILED,
            error_message="boom",
        )
        item.refresh_from_db()
        self.assertEqual(item.worker_synced_revision, 2)
        data2 = self._proxy_url(item)
        self.assertTrue(data2["has_latest_order_failed"])

        # 5. 後続の apply_edl 成功 -> worker_synced_revision=6, has_latest_order_failed=False
        order3 = create_edit_order(
            self.detail.pk,
            order_type=RecordingEditOrder.OrderType.APPLY_EDL,
            expected_revision=5,
            edl_json=_edl(start=10.0, end=55.0),
            requested_by=self.superuser,
        )
        order3.status = RecordingEditOrder.Status.RUNNING
        order3.started_at = timezone.now()
        order3.save(update_fields=["status", "started_at"])
        complete_edit_order(
            order3.pk,
            target_revision=6,
            status=RecordingEditOrder.Status.SUCCEEDED,
            result_video_id="vidNew12345",
            youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.UNLISTED,
            review_status=RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED,
        )
        item.refresh_from_db()
        self.assertEqual(item.worker_synced_revision, 6)
        data3 = self._proxy_url(item)
        self.assertEqual(data3["worker_synced_revision"], 6)
        self.assertFalse(data3["has_latest_order_failed"])
