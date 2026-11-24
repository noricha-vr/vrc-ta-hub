"""途中カット cuts[] の採否・手動編集・ALLOW_ACCEPT_CUTS・競合排他・境界検証のテスト。"""
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
from rest_framework.test import APIClient

from community.constants import RecordingPolicy
from event.models import RecordingEditOrder, RecordingReviewItem
from tests.factories import (
    make_community,
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


def _edl_with_pending_cuts() -> dict:
    return {
        "schema_version": 1,
        "safe_bounds": {"min_sec": 0.0, "max_sec": 1200.0},
        "trim": {"start_sec": 30.0, "end_sec": 900.0},
        "boundary_candidates": {
            "start": [{"sec": 30.0, "label": "挨拶開始"}],
            "end": [{"sec": 900.0, "label": "質疑終了"}],
        },
        "cuts": [
            {
                "id": "cand-1",
                "start_sec": 120.0,
                "end_sec": 150.0,
                "kind": "silence",
                "label": "長い無音",
                "source": "vad",
                "confidence": 0.92,
                "action": "pending",
            },
            {
                "id": "cand-2",
                "start_sec": 300.0,
                "end_sec": 325.0,
                "kind": "trouble",
                "label": "マイク調整",
                "source": "vad",
                "confidence": 0.78,
                "action": "pending",
            },
        ],
    }


@_r2_settings()
class RecordingCutsViewsAndOrdersTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = make_discord_linked_user(user_name="cuts_staff", email="cuts_staff@example.com", is_staff=True)
        cls.regular_user = make_discord_linked_user(user_name="cuts_user", email="cuts_user@example.com")
        cls.owner = make_discord_linked_user(user_name="cuts_owner", email="cuts_owner@example.com")
        cls.community = make_community(name="カット検証集会", owner=cls.owner, recording_allowed=True)
        cls.event = make_event(cls.community)
        cls.detail = make_event_detail(
            cls.event,
            status="approved",
            speaker="登壇者Cuts",
            theme="途中カット機能検証",
            recording_policy=RecordingPolicy.PUBLIC,
        )
        cls.forbidden_detail = make_event_detail(
            cls.event,
            status="approved",
            speaker="登壇者Forbidden",
            theme="撮影禁止",
            recording_policy=RecordingPolicy.FORBIDDEN,
        )

    def setUp(self):
        cache.clear()
        self.item = RecordingReviewItem.objects.create(
            event_detail=self.detail,
            proxy_r2_key=f"recording-proxy/{self.detail.pk}/{uuid.uuid4().hex}.mp4",
            proxy_duration_sec=1200.0,
            proxy_uploaded_at=timezone.now(),
            proxy_expires_at=timezone.now() + timedelta(days=7),
            review_status=RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
            youtube_draft_video_id="dQw4w9WgXcQ",
            youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.UNLISTED,
            edl_revision=1,
            edl_json=_edl_with_pending_cuts(),
        )
        self.detail_url = reverse("event:recording_review_detail", kwargs={"detail_id": self.detail.pk})
        self.api_order_url = reverse("recording-ops-order-create", kwargs={"detail_id": self.detail.pk})

    @patch("event.services.recording_ops.storage.boto3.client")
    def test_default_flag_blocks_accept_cuts_on_ui_and_api_with_400(self, mock_boto):
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://r2.example.invalid/signed?sig=cuts"
        mock_boto.return_value = mock_s3

        self.client.force_login(self.staff)
        get_resp = self.client.get(self.detail_url)
        self.assertEqual(get_resp.status_code, 200)
        soup = BeautifulSoup(get_resp.content, "html.parser")
        self.assertIsNotNone(soup.select_one('[data-testid="accept-cuts-disabled-notice"]'))
        config = json.loads(soup.select_one("#recording-review-config").get_text())
        self.assertFalse(config["allowAcceptCuts"])

        cuts_with_accept = [
            {**_edl_with_pending_cuts()["cuts"][0], "action": "accept"},
            {**_edl_with_pending_cuts()["cuts"][1], "action": "reject"},
        ]
        # Web JSON POST で accept カットは 400 accept_cuts_disabled
        json_resp = self.client.post(
            self.detail_url,
            data=json.dumps(
                {
                    "expected_revision": 1,
                    "start_sec": 30.0,
                    "end_sec": 900.0,
                    "cuts": cuts_with_accept,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(json_resp.status_code, 400)
        self.assertEqual(json_resp.json()["code"], "accept_cuts_disabled")

        # Form POST でも 400 かつ送信した cuts_json が復元される
        form_resp = self.client.post(
            self.detail_url,
            {
                "expected_revision": "1",
                "start_sec": "30",
                "end_sec": "900",
                "cuts_json": json.dumps(cuts_with_accept),
            },
        )
        self.assertEqual(form_resp.status_code, 400)
        form_soup = BeautifulSoup(form_resp.content, "html.parser")
        self.assertIn("accept", form_soup.select_one("#id_cuts_json")["value"])
        self.item.refresh_from_db()
        self.assertEqual(self.item.edl_revision, 1)
        self.assertEqual(RecordingEditOrder.objects.count(), 0)

        # API v1 POST /orders/ でも 400 accept_cuts_disabled（edl_json あり・なし双方）
        api_client = APIClient()
        api_client.force_login(self.staff)
        edl_accept = _edl_with_pending_cuts()
        edl_accept["cuts"][0]["action"] = "accept"
        api_resp = api_client.post(
            self.api_order_url,
            {"expected_revision": 1, "order_type": "apply_edl", "edl_json": edl_accept},
            format="json",
        )
        self.assertEqual(api_resp.status_code, 400)
        self.assertEqual(api_resp.data["code"], "accept_cuts_disabled")

        # 既に accept を含む edl_json が保存済みの状態で edl_json 省略 apply_edl も 400 拒否
        self.item.edl_json = edl_accept
        self.item.save(update_fields=["edl_json"])
        api_omit_resp = api_client.post(
            self.api_order_url,
            {"expected_revision": 1, "order_type": "apply_edl"},
            format="json",
        )
        self.assertEqual(api_omit_resp.status_code, 400)
        self.assertEqual(api_omit_resp.data["code"], "accept_cuts_disabled")

        # 緊急停止 stop_publish は accept カットが残っていても通る
        stop_resp = api_client.post(
            self.api_order_url,
            {"expected_revision": 1, "order_type": "stop_publish"},
            format="json",
        )
        self.assertEqual(stop_resp.status_code, 201)

    @patch("event.services.recording_ops.storage.boto3.client")
    def test_pending_and_reject_cuts_submit_allowed_when_flag_off(self, mock_boto):
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://r2.example.invalid/signed?sig=ok"
        mock_boto.return_value = mock_s3

        self.client.force_login(self.staff)
        cuts_no_accept = [
            {**_edl_with_pending_cuts()["cuts"][0], "action": "reject", "label": "あ" * 150},
            {**_edl_with_pending_cuts()["cuts"][1], "action": "pending"},
        ]
        resp = self.client.post(
            self.detail_url,
            data=json.dumps(
                {
                    "expected_revision": 1,
                    "start_sec": 30.0,
                    "end_sec": 900.0,
                    "cuts": cuts_no_accept,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["edl_revision"], 2)
        self.assertEqual([c["action"] for c in data["cuts"]], ["reject", "pending"])
        self.assertEqual(data["keep_spans"], [[30.0, 900.0]])
        self.assertAlmostEqual(data["adopted_duration_sec"], 870.0)

    @patch("event.services.recording_ops.edl.ALLOW_ACCEPT_CUTS", True)
    @patch("event.services.recording_ops.storage.boto3.client")
    def test_accept_and_manual_cuts_submit_with_logging_and_validation(self, mock_boto):
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://r2.example.invalid/signed?sig=cuts2"
        mock_boto.return_value = mock_s3

        self.client.force_login(self.staff)
        cuts_payload = [
            {**_edl_with_pending_cuts()["cuts"][0], "action": "accept"},
            {**_edl_with_pending_cuts()["cuts"][1], "action": "reject"},
            {
                "id": "manual-1",
                "start_sec": 500.0,
                "end_sec": 520.0,
                "kind": "break",
                "label": "水分補給",
                "source": "staff",
                "action": "accept",
            },
        ]
        with self.assertLogs("event.views.recording_review", level="INFO") as logs:
            resp = self.client.post(
                self.detail_url,
                data=json.dumps(
                    {
                        "expected_revision": 1,
                        "start_sec": 30.0,
                        "end_sec": 900.0,
                        "cuts": cuts_payload,
                    }
                ),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["edl_revision"], 2)
        self.assertEqual(body["keep_spans"], [[30.0, 120.0], [150.0, 500.0], [520.0, 900.0]])
        self.assertAlmostEqual(body["adopted_duration_sec"], 820.0)
        self.assertIn("recording_review_trim_order_submitted", "\n".join(logs.output))

        order = RecordingEditOrder.objects.get(pk=body["order_id"])
        self.assertEqual(len(order.edl_snapshot["cuts"]), 3)
        self.assertEqual(order.edl_snapshot["cuts"][2]["source"], "staff")

        # 完了後に不正リクエスト群を 400 で弾くことを検証
        order.status = RecordingEditOrder.Status.SUCCEEDED
        order.active_lock_key = None
        order.save(update_fields=["status", "active_lock_key"])

        bad_cases = [
            # trim 範囲外
            [{"id": "c1", "start_sec": 10.0, "end_sec": 50.0, "kind": "silence", "label": "外", "action": "accept"}],
            # accept 重複
            [
                {"id": "c1", "start_sec": 100.0, "end_sec": 150.0, "kind": "silence", "label": "1", "action": "accept"},
                {"id": "c2", "start_sec": 140.0, "end_sec": 180.0, "kind": "silence", "label": "2", "action": "accept"},
            ],
            # accept 順序逆転
            [
                {"id": "c1", "start_sec": 200.0, "end_sec": 220.0, "kind": "silence", "label": "1", "action": "accept"},
                {"id": "c2", "start_sec": 100.0, "end_sec": 120.0, "kind": "silence", "label": "2", "action": "accept"},
            ],
            # 全区間カット（keep_spans < 1.0 秒）
            [{"id": "c1", "start_sec": 30.0, "end_sec": 900.0, "kind": "silence", "label": "全", "action": "accept"}],
            # 不正アクション
            [{"id": "c1", "start_sec": 100.0, "end_sec": 120.0, "kind": "silence", "label": "x", "action": "delete"}],
        ]
        for bad_cuts in bad_cases:
            with self.subTest(bad_cuts=bad_cuts):
                bad_resp = self.client.post(
                    self.detail_url,
                    data=json.dumps({"expected_revision": 2, "start_sec": 30.0, "end_sec": 900.0, "cuts": bad_cuts}),
                    content_type="application/json",
                )
                self.assertEqual(bad_resp.status_code, 400)

        # Form POST でも不正 cuts_json は 400、期待リビジョン不一致は 409
        bad_form = self.client.post(
            self.detail_url,
            {"expected_revision": "2", "start_sec": "30", "end_sec": "900", "cuts_json": "{broken"},
        )
        self.assertEqual(bad_form.status_code, 400)

        conflict_form = self.client.post(
            self.detail_url,
            {"expected_revision": "1", "start_sec": "30", "end_sec": "900", "cuts_json": json.dumps([])},
        )
        self.assertEqual(conflict_form.status_code, 409)

        # 権限・同意境界の維持確認
        self.client.force_login(self.regular_user)
        self.assertEqual(
            self.client.post(
                self.detail_url,
                data=json.dumps({"expected_revision": 2, "start_sec": 30.0, "end_sec": 900.0, "cuts": []}),
                content_type="application/json",
            ).status_code,
            403,
        )
        self.client.force_login(self.staff)
        forbidden_url = reverse("event:recording_review_detail", kwargs={"detail_id": self.forbidden_detail.pk})
        self.assertEqual(
            self.client.post(
                forbidden_url,
                data=json.dumps({"expected_revision": 1, "start_sec": 30.0, "end_sec": 900.0, "cuts": []}),
                content_type="application/json",
            ).status_code,
            403,
        )
