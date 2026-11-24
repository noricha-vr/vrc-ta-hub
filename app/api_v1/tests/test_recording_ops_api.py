"""動画編集プロキシ R2 署名配信・EDL v1 確定指示キュー API の結合テスト。"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.admin.sites import AdminSite
from django.core.cache import cache
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from community.constants import RecordingPolicy
from event.admin import RecordingEditOrderAdmin, RecordingReviewItemAdmin
from event.models import EventDetail, RecordingEditOrder, RecordingReviewItem
from event.services.recurrence_override import delete_event_with_tombstones
from tests.factories import make_community, make_event, make_event_detail, make_user
from user_account.models import APIKey


def _r2_settings():
    return override_settings(
        AWS_ACCESS_KEY_ID="test-r2-key",
        AWS_SECRET_ACCESS_KEY="test-r2-secret",
        AWS_STORAGE_BUCKET_NAME="vrc-ta-hub-public",
        AWS_PRIVATE_STORAGE_BUCKET_NAME="vrc-ta-hub-private",
        AWS_S3_ENDPOINT_URL="https://r2.example.invalid",
    )


def _valid_edl(start_sec: float = 30.0, end_sec: float = 900.0) -> dict:
    return {
        "schema_version": 1,
        "safe_bounds": {"min_sec": 0.0, "max_sec": 1200.0},
        "trim": {"start_sec": start_sec, "end_sec": end_sec},
        "boundary_candidates": {"start": [30.0], "end": [900.0]},
        "cuts": [],
    }


@_r2_settings()
class RecordingOpsAPITest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = make_user(
            user_name="rec_super",
            email="rec_super@example.com",
            is_staff=True,
            is_superuser=True,
        )
        cls.staff = make_user(
            user_name="rec_staff",
            email="rec_staff@example.com",
            is_staff=True,
            is_superuser=False,
        )
        cls.community_owner = make_user(
            user_name="rec_owner",
            email="rec_owner@example.com",
            is_staff=False,
            is_superuser=False,
        )
        cls.regular_user = make_user(
            user_name="rec_regular",
            email="rec_regular@example.com",
            is_staff=False,
            is_superuser=False,
        )

        cls.community = make_community(
            name="録画編集集会",
            owner=cls.community_owner,
            recording_allowed=True,
        )
        cls.disallowed_community = make_community(
            name="非許可集会",
            owner=cls.community_owner,
            recording_allowed=False,
        )
        cls.event = make_event(cls.community)
        cls.disallowed_event = make_event(cls.disallowed_community)

        _, cls.super_write_key = APIKey.create_with_raw_key(user=cls.superuser, name="super-write")
        super_read_obj, cls.super_read_key = APIKey.create_with_raw_key(user=cls.superuser, name="super-read")
        super_read_obj.scope = APIKey.SCOPE_READ
        super_read_obj.save(update_fields=["scope"])

        _, cls.staff_write_key = APIKey.create_with_raw_key(user=cls.staff, name="staff-write")
        staff_read_obj, cls.staff_read_key = APIKey.create_with_raw_key(user=cls.staff, name="staff-read")
        staff_read_obj.scope = APIKey.SCOPE_READ
        staff_read_obj.save(update_fields=["scope"])

        _, cls.owner_write_key = APIKey.create_with_raw_key(user=cls.community_owner, name="owner-write")

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.detail = make_event_detail(
            self.event,
            status="approved",
            recording_policy=RecordingPolicy.PUBLIC,
        )
        self.upload_url = reverse("recording-ops-proxy-upload-url", kwargs={"detail_id": self.detail.pk})
        self.upsert_url = reverse("recording-ops-item-upsert", kwargs={"detail_id": self.detail.pk})
        self.proxy_get_url = reverse("recording-ops-proxy-url", kwargs={"detail_id": self.detail.pk})
        self.order_create_url = reverse("recording-ops-order-create", kwargs={"detail_id": self.detail.pk})
        self.order_claim_url = reverse("recording-ops-order-claim")

    def _api_client(self, raw_key: str) -> APIClient:
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw_key}")
        return client

    def _valid_proxy_key(self, detail_id: int | None = None) -> str:
        target = detail_id or self.detail.pk
        return f"recording-proxy/{target}/{uuid.uuid4().hex}.mp4"

    def _register_item(self, detail: EventDetail | None = None, **extra) -> RecordingReviewItem:
        target = detail or self.detail
        payload = {
            "proxy_r2_key": self._valid_proxy_key(target.pk),
            "proxy_duration_sec": 1200.0,
            "edl_json": _valid_edl(),
            "youtube_draft_video_id": "dQw4w9WgXcQ",
            "youtube_draft_privacy": "unlisted",
        }
        payload.update(extra)
        url = reverse("recording-ops-item-upsert", kwargs={"detail_id": target.pk})
        resp = self._api_client(self.super_write_key).put(url, payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        return RecordingReviewItem.objects.get(event_detail=target)

    @patch("event.services.recording_storage.boto3.client")
    def test_auth_scope_and_permission_matrix(self, mock_boto_client):
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://r2.example.invalid/signed"
        mock_boto_client.return_value = mock_s3

        item = self._register_item(youtube_draft_video_id="", youtube_draft_privacy="")

        stop_item_url = reverse("recording-ops-review-item-stop-publish", kwargs={"review_item_id": item.pk})

        # 未認証は 401/403 + code
        anon = APIClient()
        for method, url in (
            ("post", self.upload_url),
            ("put", self.upsert_url),
            ("get", self.proxy_get_url),
            ("post", self.order_create_url),
            ("post", stop_item_url),
            ("post", self.order_claim_url),
            ("post", reverse("recording-ops-order-complete", kwargs={"order_id": 1})),
        ):
            resp = getattr(anon, method)(url, {}, format="json")
            self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
            self.assertIn("code", resp.data)

        # 一般ユーザー・集会オーナー（is_staff=False）は全 7 エンドポイント 403
        for client_user, key in ((self.regular_user, None), (self.community_owner, self.owner_write_key)):
            session_c = APIClient()
            session_c.force_login(client_user)
            clients = [session_c]
            if key:
                clients.append(self._api_client(key))
            for client in clients:
                for method, url in (
                    ("post", self.upload_url),
                    ("put", self.upsert_url),
                    ("get", self.proxy_get_url),
                    ("post", self.order_create_url),
                    ("post", stop_item_url),
                    ("post", self.order_claim_url),
                    ("post", reverse("recording-ops-order-complete", kwargs={"order_id": 1})),
                ):
                    resp = getattr(client, method)(url, {}, format="json")
                    self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        # スタッフ（is_staff=True, is_superuser=False）:
        # GET proxy-url と POST orders は通るが、4 つのワーカー向けエンドポイントは 403
        staff_session = APIClient()
        staff_session.force_login(self.staff)
        self.assertEqual(staff_session.get(self.proxy_get_url).status_code, status.HTTP_200_OK)
        self.assertEqual(
            staff_session.post(self.order_create_url, {"expected_revision": item.edl_revision}, format="json").status_code,
            status.HTTP_201_CREATED,
        )
        for method, url in (
            ("post", self.upload_url),
            ("put", self.upsert_url),
            ("post", self.order_claim_url),
            ("post", reverse("recording-ops-order-complete", kwargs={"order_id": 1})),
        ):
            for client in (staff_session, self._api_client(self.staff_write_key)):
                resp = getattr(client, method)(url, {}, format="json")
                self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        # staff の read キーは GET proxy-url 可・POST orders / stop-publish 不可
        staff_read = self._api_client(self.staff_read_key)
        self.assertEqual(staff_read.get(self.proxy_get_url).status_code, status.HTTP_200_OK)
        read_order_resp = staff_read.post(self.order_create_url, {"expected_revision": 2}, format="json")
        self.assertEqual(read_order_resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(read_order_resp.data["code"], "read_only_api_key")
        read_stop_resp = staff_read.post(stop_item_url, {"expected_revision": 2}, format="json")
        self.assertEqual(read_stop_resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(read_stop_resp.data["code"], "read_only_api_key")

        # superuser の read キーは POST/PUT 不可、session auth は APIKey 限定 4 エンドポイント不可
        super_read = self._api_client(self.super_read_key)
        for method, url in (
            ("post", self.upload_url),
            ("put", self.upsert_url),
            ("post", self.order_claim_url),
            ("post", reverse("recording-ops-order-complete", kwargs={"order_id": 1})),
        ):
            resp = getattr(super_read, method)(url, {}, format="json")
            self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(resp.data["code"], "read_only_api_key")

        super_session = APIClient()
        super_session.force_login(self.superuser)
        for method, url in (
            ("post", self.upload_url),
            ("put", self.upsert_url),
            ("post", self.order_claim_url),
            ("post", reverse("recording-ops-order-complete", kwargs={"order_id": 1})),
        ):
            resp = getattr(super_session, method)(url, {}, format="json")
            self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_csrf_enforced_for_session_but_exempt_for_api_key(self):
        item = self._register_item(youtube_draft_video_id="", youtube_draft_privacy="")

        session_csrf = APIClient(enforce_csrf_checks=True)
        session_csrf.force_login(self.staff)
        resp_session = session_csrf.post(
            self.order_create_url,
            {"expected_revision": item.edl_revision},
            format="json",
        )
        self.assertEqual(resp_session.status_code, status.HTTP_403_FORBIDDEN)

        api_csrf = APIClient(enforce_csrf_checks=True)
        api_csrf.credentials(HTTP_AUTHORIZATION=f"Bearer {self.staff_write_key}")
        resp_api = api_csrf.post(
            self.order_create_url,
            {"expected_revision": item.edl_revision},
            format="json",
        )
        self.assertEqual(resp_api.status_code, status.HTTP_201_CREATED)

    @patch("event.services.recording_storage.boto3.client")
    def test_consent_boundary_rejects_allowed_forbidden_and_disallowed_community(self, mock_boto_client):
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://r2.example.invalid/signed"
        mock_boto_client.return_value = mock_s3

        super_client = self._api_client(self.super_write_key)
        staff_client = self._api_client(self.staff_write_key)

        for policy in (RecordingPolicy.ALLOWED, RecordingPolicy.FORBIDDEN):
            detail = make_event_detail(self.event, status="approved", recording_policy=policy)
            with self.subTest(policy=policy):
                for client, method, url, body in (
                    (super_client, "post", reverse("recording-ops-proxy-upload-url", kwargs={"detail_id": detail.pk}), {}),
                    (
                        super_client,
                        "put",
                        reverse("recording-ops-item-upsert", kwargs={"detail_id": detail.pk}),
                        {"proxy_r2_key": self._valid_proxy_key(detail.pk), "edl_json": _valid_edl()},
                    ),
                    (staff_client, "get", reverse("recording-ops-proxy-url", kwargs={"detail_id": detail.pk}), None),
                    (
                        staff_client,
                        "post",
                        reverse("recording-ops-order-create", kwargs={"detail_id": detail.pk}),
                        {"expected_revision": 1},
                    ),
                ):
                    resp = getattr(client, method)(url, body, format="json") if body is not None else client.get(url)
                    self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
                    self.assertEqual(resp.data["code"], "recording_not_permitted")

        disallowed_detail = make_event_detail(
            self.disallowed_event,
            status="approved",
            recording_policy=RecordingPolicy.PUBLIC,
        )
        resp = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": disallowed_detail.pk}),
            {"expected_revision": 1},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(resp.data["code"], "recording_not_permitted")

    @patch("event.services.recording_storage.boto3.client")
    def test_upload_upsert_and_proxy_get_lifecycle(self, mock_boto_client):
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://r2.example.invalid/vrc-ta-hub-private/signed"
        mock_boto_client.return_value = mock_s3

        super_client = self._api_client(self.super_write_key)
        staff_client = self._api_client(self.staff_write_key)

        upload_resp = super_client.post(self.upload_url, {"expires_in": 300}, format="json")
        self.assertEqual(upload_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(upload_resp.data["expires_in"], 900)
        object_key = upload_resp.data["object_key"]

        # 不正なオブジェクトキーは拒否
        bad_key_resp = super_client.put(
            self.upsert_url,
            {"proxy_r2_key": f"recording-proxy/{self.detail.pk + 1}/{uuid.uuid4().hex}.mp4", "edl_json": _valid_edl()},
            format="json",
        )
        self.assertEqual(bad_key_resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(bad_key_resp.data["code"], "invalid_object_key")

        # 初回登録 -> 201
        upsert_resp = super_client.put(
            self.upsert_url,
            {
                "proxy_r2_key": object_key,
                "proxy_duration_sec": 1200.0,
                "edl_json": _valid_edl(),
            },
            format="json",
        )
        self.assertEqual(upsert_resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(upsert_resp.data["edl_revision"], 1)

        # 更新時の expected_revision 不一致 -> 409
        conflict_resp = super_client.put(
            self.upsert_url,
            {"edl_json": _valid_edl(start_sec=40.0), "expected_revision": 99},
            format="json",
        )
        self.assertEqual(conflict_resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(conflict_resp.data["code"], "revision_conflict")

        # 正常更新 -> edl_revision=2
        update_resp = super_client.put(
            self.upsert_url,
            {"edl_json": _valid_edl(start_sec=45.0), "expected_revision": 1},
            format="json",
        )
        self.assertEqual(update_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(update_resp.data["edl_revision"], 2)

        # GET 署名 URL（有効期限クランプ確認）
        get_resp = staff_client.get(f"{self.proxy_get_url}?expires_in=9999")
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(get_resp.data["expires_in"], 1800)
        self.assertEqual(get_resp.data["edl_revision"], 2)
        self.assertNotIn("proxy_r2_key", get_resp.data)

        # 不正クエリ -> 400
        bad_query = staff_client.get(f"{self.proxy_get_url}?expires_in=abc")
        self.assertEqual(bad_query.status_code, status.HTTP_400_BAD_REQUEST)

        # 期限切れ -> 410
        RecordingReviewItem.objects.filter(event_detail=self.detail).update(
            proxy_expires_at=timezone.now() - timedelta(minutes=1)
        )
        expired_resp = staff_client.get(self.proxy_get_url)
        self.assertEqual(expired_resp.status_code, status.HTTP_410_GONE)
        self.assertEqual(expired_resp.data["code"], "proxy_expired")

    def test_order_conflict_revision_mismatch_active_order_and_public_lock(self):
        staff_client = self._api_client(self.staff_write_key)
        super_client = self._api_client(self.super_write_key)

        # プロキシ未登録時は 409
        not_ready = staff_client.post(self.order_create_url, {"expected_revision": 1}, format="json")
        self.assertEqual(not_ready.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(not_ready.data["code"], "proxy_not_ready")

        item = self._register_item(youtube_draft_video_id="", youtube_draft_privacy="")

        # expected_revision に bool/欠落 -> 400
        for bad_body in ({}, {"expected_revision": True}, {"expected_revision": "1"}):
            resp = staff_client.post(self.order_create_url, bad_body, format="json")
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        # expected_revision 不一致 -> 409
        mismatch = staff_client.post(self.order_create_url, {"expected_revision": 99}, format="json")
        self.assertEqual(mismatch.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(mismatch.data["code"], "revision_conflict")
        self.assertEqual(mismatch.data["current"]["edl_revision"], 1)

        # 1 回目確定成功 -> rev 1 -> 2, order queued
        new_edl = _valid_edl(start_sec=60.0, end_sec=840.0)
        created = staff_client.post(
            self.order_create_url,
            {"expected_revision": 1, "edl_json": new_edl},
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        self.assertEqual(created.data["base_revision"], 1)
        self.assertEqual(created.data["target_revision"], 2)
        self.assertEqual(created.data["status"], "queued")
        self.assertEqual(created.data["edl_snapshot"]["trim"]["start_sec"], 60.0)

        # queued 中は最新 rev=2 を送っても 409 active_order_exists
        dup_resp = staff_client.post(self.order_create_url, {"expected_revision": 2}, format="json")
        self.assertEqual(dup_resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(dup_resp.data["code"], "active_order_exists")

        # queued 中の PUT も 409 active_order_exists
        put_during_order = super_client.put(
            self.upsert_url,
            {"edl_json": _valid_edl(start_sec=70.0), "expected_revision": 2},
            format="json",
        )
        self.assertEqual(put_during_order.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(put_during_order.data["code"], "active_order_exists")

        # claim -> running 中も 409 active_order_exists
        claim = super_client.post(self.order_claim_url, {}, format="json")
        self.assertEqual(claim.status_code, status.HTTP_200_OK)
        self.assertEqual(claim.data["order"]["id"], created.data["id"])
        self.assertEqual(claim.data["order"]["status"], "running")

        dup_running = staff_client.post(self.order_create_url, {"expected_revision": 2}, format="json")
        self.assertEqual(dup_running.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(dup_running.data["code"], "active_order_exists")

        # complete (succeeded, public 化 + youtube_url 更新)
        complete_url = reverse("recording-ops-order-complete", kwargs={"order_id": created.data["id"]})
        # target_revision 不一致 -> 409
        bad_rev = super_client.post(
            complete_url,
            {"target_revision": 1, "status": "succeeded", "youtube_url": "https://youtu.be/abcdefghijk"},
            format="json",
        )
        self.assertEqual(bad_rev.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(bad_rev.data["code"], "target_revision_mismatch")

        # 不正 YouTube URL -> 400
        for bad_yt in (
            "http://youtu.be/abcdefghijk",
            "https://evil.example/watch?v=abcdefghijk",
            "https://user:pass@youtu.be/abcdefghijk",
        ):
            bad_url_resp = super_client.post(
                complete_url,
                {"target_revision": 2, "status": "succeeded", "youtube_url": bad_yt},
                format="json",
            )
            self.assertEqual(bad_url_resp.status_code, status.HTTP_400_BAD_REQUEST)

        # 正常完了 -> EventDetail.youtube_url 更新・公開ロック
        ok_resp = super_client.post(
            complete_url,
            {
                "target_revision": 2,
                "status": "succeeded",
                "result_video_id": "abcdefghijk",
                "youtube_url": "https://youtu.be/abcdefghijk",
                "youtube_draft_privacy": "public",
            },
            format="json",
        )
        self.assertEqual(ok_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(ok_resp.data["status"], "succeeded")

        self.detail.refresh_from_db()
        self.assertEqual(self.detail.youtube_url, "https://youtu.be/abcdefghijk")
        item.refresh_from_db()
        self.assertEqual(item.review_status, RecordingReviewItem.ReviewStatus.PUBLISHED)
        self.assertTrue(item.is_public_locked)

        # 完了済みの再報告は 409 order_not_running
        recomplete = super_client.post(
            complete_url,
            {"target_revision": 2, "status": "succeeded"},
            format="json",
        )
        self.assertEqual(recomplete.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(recomplete.data["code"], "order_not_running")

        # 公開済みロック時は apply_edl 拒否（409 public_locked）、stop_publish は許可
        locked_resp = staff_client.post(
            self.order_create_url,
            {"expected_revision": 2, "order_type": "apply_edl"},
            format="json",
        )
        self.assertEqual(locked_resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(locked_resp.data["code"], "public_locked")

        stop_resp = staff_client.post(
            self.order_create_url,
            {"expected_revision": 2, "order_type": "stop_publish"},
            format="json",
        )
        self.assertEqual(stop_resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(stop_resp.data["order_type"], "stop_publish")

        # stop_publish を claim しても、unlisted 完了報告は 400 で拒否する
        stop_claim = super_client.post(self.order_claim_url, {}, format="json")
        self.assertEqual(stop_claim.data["order"]["id"], stop_resp.data["id"])
        stop_unlisted = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": stop_resp.data["id"]}),
            {"target_revision": 3, "status": "succeeded", "youtube_draft_privacy": "unlisted"},
            format="json",
        )
        self.assertEqual(stop_unlisted.status_code, status.HTTP_400_BAD_REQUEST)

        # stop_publish 完了時は private_fixed とともに EventDetail.youtube_url も除去し、公開済みロックは維持される
        stop_complete = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": stop_resp.data["id"]}),
            {"target_revision": 3, "status": "succeeded"},
            format="json",
        )
        self.assertEqual(stop_complete.status_code, status.HTTP_200_OK)
        self.detail.refresh_from_db()
        self.assertIsNone(self.detail.youtube_url)
        item.refresh_from_db()
        self.assertEqual(item.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)
        self.assertEqual(item.youtube_draft_privacy, RecordingReviewItem.YouTubePrivacy.PRIVATE)
        self.assertTrue(item.is_public_locked)

        after_stop_apply = staff_client.post(
            self.order_create_url,
            {"expected_revision": 3, "order_type": "apply_edl"},
            format="json",
        )
        self.assertEqual(after_stop_apply.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(after_stop_apply.data["code"], "public_locked")

    def test_order_rejects_expanded_safe_bounds_and_malformed_edl_inputs(self):
        staff_client = self._api_client(self.staff_write_key)
        self._register_item()

        # safe_bounds を登録済み (0〜1200) から拡大/変更しようとすると 400
        expanded = _valid_edl(start_sec=10.0, end_sec=1300.0)
        expanded["safe_bounds"] = {"min_sec": 0.0, "max_sec": 1400.0}
        resp_expanded = staff_client.post(
            self.order_create_url,
            {"expected_revision": 1, "edl_json": expanded},
            format="json",
        )
        self.assertEqual(resp_expanded.status_code, status.HTTP_400_BAD_REQUEST)

        # unhashable action / 巨大整数でも 500 にならず 400
        bad_action_edl = _valid_edl()
        bad_action_edl["cuts"] = [
            {"id": "c1", "start_sec": 40.0, "end_sec": 50.0, "kind": "silence", "label": "x", "action": []}
        ]
        self.assertEqual(
            staff_client.post(
                self.order_create_url,
                {"expected_revision": 1, "edl_json": bad_action_edl},
                format="json",
            ).status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            staff_client.post(
                self.order_create_url,
                {"expected_revision": 10**400},
                format="json",
            ).status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_claim_cancels_orders_invalidated_by_consent_delete_or_expiry_and_picks_next(self):
        staff_client = self._api_client(self.staff_write_key)
        super_client = self._api_client(self.super_write_key)

        detail_deleted = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        detail_expired = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        detail_valid = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)

        self._register_item(self.detail)
        self._register_item(detail_deleted)
        self._register_item(detail_expired)
        self._register_item(detail_valid)

        o_consent = staff_client.post(self.order_create_url, {"expected_revision": 1}, format="json").data["id"]
        o_deleted = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail_deleted.pk}),
            {"expected_revision": 1},
            format="json",
        ).data["id"]
        o_expired = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail_expired.pk}),
            {"expected_revision": 1},
            format="json",
        ).data["id"]
        o_valid = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail_valid.pk}),
            {"expected_revision": 1},
            format="json",
        ).data["id"]

        # キュー投入後に同意撤回・論理削除・期限切れを発生させる
        EventDetail.objects.filter(pk=self.detail.pk).update(recording_policy=RecordingPolicy.FORBIDDEN)
        detail_deleted.soft_delete()
        RecordingReviewItem.objects.filter(event_detail=detail_expired).update(
            proxy_expires_at=timezone.now() - timedelta(seconds=1)
        )

        claimed = super_client.post(self.order_claim_url, {}, format="json")
        self.assertEqual(claimed.status_code, status.HTTP_200_OK)
        self.assertEqual(claimed.data["order"]["id"], o_valid)

        for canceled_id in (o_consent, o_deleted, o_expired):
            order = RecordingEditOrder.objects.get(pk=canceled_id)
            self.assertEqual(order.status, RecordingEditOrder.Status.CANCELED)
            self.assertIsNone(order.active_lock_key)

    def test_recording_models_admin_is_readonly(self):
        request = RequestFactory().get("/admin/")
        request.user = self.superuser
        for admin_cls, model in (
            (RecordingReviewItemAdmin, RecordingReviewItem),
            (RecordingEditOrderAdmin, RecordingEditOrder),
        ):
            model_admin = admin_cls(model, AdminSite())
            self.assertFalse(model_admin.has_add_permission(request))
            self.assertFalse(model_admin.has_change_permission(request))
            self.assertFalse(model_admin.has_delete_permission(request))

    def test_claim_fifo_empty_queue_and_failed_retry_flow(self):
        staff_client = self._api_client(self.staff_write_key)
        super_client = self._api_client(self.super_write_key)

        # 空キュー
        empty = super_client.post(self.order_claim_url, {}, format="json")
        self.assertEqual(empty.status_code, status.HTTP_200_OK)
        self.assertIsNone(empty.data["order"])

        detail2 = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        self._register_item(self.detail)
        self._register_item(detail2)

        o1 = staff_client.post(self.order_create_url, {"expected_revision": 1}, format="json")
        o2 = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail2.pk}),
            {"expected_revision": 1},
            format="json",
        )
        self.assertEqual(o1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(o2.status_code, status.HTTP_201_CREATED)

        # 古い順に claim される
        c1 = super_client.post(self.order_claim_url, {}, format="json")
        c2 = super_client.post(self.order_claim_url, {}, format="json")
        c3 = super_client.post(self.order_claim_url, {}, format="json")
        self.assertEqual(c1.data["order"]["id"], o1.data["id"])
        self.assertEqual(c2.data["order"]["id"], o2.data["id"])
        self.assertIsNone(c3.data["order"])

        # o1 を failed 報告 -> ロック解放され、rev=2 で再確定指示できる
        fail_resp = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": o1.data["id"]}),
            {"target_revision": 2, "status": "failed", "error_message": "encode failed"},
            format="json",
        )
        self.assertEqual(fail_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(fail_resp.data["status"], "failed")
        self.assertEqual(fail_resp.data["error_message"], "encode failed")

        order1_db = RecordingEditOrder.objects.get(pk=o1.data["id"])
        self.assertIsNone(order1_db.active_lock_key)

        retry_resp = staff_client.post(self.order_create_url, {"expected_revision": 2}, format="json")
        self.assertEqual(retry_resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(retry_resp.data["base_revision"], 2)
        self.assertEqual(retry_resp.data["target_revision"], 3)

    def test_soft_deleted_detail_returns_404_and_consent_revoked_triggers_compensating_stop(self):
        staff_client = self._api_client(self.staff_write_key)
        super_client = self._api_client(self.super_write_key)

        item = self._register_item(youtube_draft_video_id="", youtube_draft_privacy="")
        order = staff_client.post(self.order_create_url, {"expected_revision": 1}, format="json")
        self.assertEqual(order.status_code, status.HTTP_201_CREATED)
        super_client.post(self.order_claim_url, {}, format="json")

        # 実行中に発表同意が forbidden へ撤回された場合（youtube_url 省略でも補償 stop_publish を投入する）
        EventDetail.objects.filter(pk=self.detail.pk).update(recording_policy=RecordingPolicy.FORBIDDEN)
        complete_resp = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": order.data["id"]}),
            {
                "target_revision": 2,
                "status": "succeeded",
                "result_video_id": "abcdefghijk",
                "youtube_draft_privacy": "public",
            },
            format="json",
        )
        self.assertEqual(complete_resp.status_code, status.HTTP_200_OK)
        self.detail.refresh_from_db()
        self.assertIsNone(self.detail.youtube_url)
        item.refresh_from_db()
        self.assertEqual(item.edl_revision, 3)

        # 自動投入された補償 stop_publish を claim/complete して private_fixed へ落とせる
        comp_claim = super_client.post(self.order_claim_url, {}, format="json")
        self.assertIsNotNone(comp_claim.data["order"])
        self.assertEqual(comp_claim.data["order"]["order_type"], RecordingEditOrder.OrderType.STOP_PUBLISH)
        comp_done = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": comp_claim.data["order"]["id"]}),
            {"target_revision": 3, "status": "succeeded"},
            format="json",
        )
        self.assertEqual(comp_done.status_code, status.HTTP_200_OK)
        item.refresh_from_db()
        self.assertEqual(item.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)
        self.assertEqual(item.youtube_draft_privacy, RecordingReviewItem.YouTubePrivacy.PRIVATE)

        # 別の発表で集会許可撤回・論理削除の補償経路も確認（youtube_url 既存値があれば除去）
        detail2 = make_event_detail(
            self.event,
            speaker="発表者2",
            theme="補償停止テスト",
            status="approved",
            recording_policy=RecordingPolicy.PUBLIC,
            youtube_url="https://youtu.be/legacyvid01",
        )
        item2 = self._register_item(detail2, youtube_draft_video_id="", youtube_draft_privacy="")
        order2 = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail2.pk}),
            {"expected_revision": 1},
            format="json",
        )
        self.assertEqual(order2.status_code, status.HTTP_201_CREATED)
        super_client.post(self.order_claim_url, {}, format="json")
        self.community.recording_allowed = False
        self.community.save(update_fields=["recording_allowed"])
        resp2 = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": order2.data["id"]}),
            {
                "target_revision": 2,
                "status": "succeeded",
                "youtube_url": "https://youtu.be/newpublic11",
                "youtube_draft_privacy": "public",
            },
            format="json",
        )
        self.assertEqual(resp2.status_code, status.HTTP_200_OK, resp2.data)
        detail2.refresh_from_db()
        self.assertIsNone(detail2.youtube_url)
        self.assertTrue(
            RecordingEditOrder.objects.filter(
                review_item=item2,
                order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
                status=RecordingEditOrder.Status.QUEUED,
            ).exists()
        )
        # claim してキューを消化したうえで、論理削除後 apply_edl 実行中完了の補償停止と手動 stop_publish を確認
        claim2 = super_client.post(self.order_claim_url, {}, format="json")
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": claim2.data["order"]["id"]}),
            {"target_revision": claim2.data["order"]["target_revision"], "status": "succeeded"},
            format="json",
        )

        self.community.recording_allowed = True
        self.community.save(update_fields=["recording_allowed"])
        detail3 = make_event_detail(
            self.event,
            speaker="発表者3",
            theme="論理削除時停止テスト",
            status="approved",
            recording_policy=RecordingPolicy.PUBLIC,
        )
        item3 = self._register_item(detail3, youtube_draft_video_id="", youtube_draft_privacy="")
        order3 = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail3.pk}),
            {"expected_revision": 1},
            format="json",
        )
        super_client.post(self.order_claim_url, {}, format="json")
        detail3.soft_delete()
        resp3 = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": order3.data["id"]}),
            {
                "target_revision": 2,
                "status": "succeeded",
                "result_video_id": "deletedvid0",
                "youtube_draft_privacy": "public",
            },
            format="json",
        )
        self.assertEqual(resp3.status_code, status.HTTP_200_OK, resp3.data)
        self.assertTrue(
            RecordingEditOrder.objects.filter(
                review_item=item3,
                order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
                status=RecordingEditOrder.Status.QUEUED,
            ).exists()
        )
        claim3 = super_client.post(self.order_claim_url, {}, format="json")
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": claim3.data["order"]["id"]}),
            {"target_revision": claim3.data["order"]["target_revision"], "status": "succeeded"},
            format="json",
        )

        # 論理削除後は通常操作と apply_edl は 404 だが、既存動画がある場合の stop_publish は手動作成できる
        self.assertEqual(
            staff_client.get(reverse("recording-ops-proxy-url", kwargs={"detail_id": detail3.pk})).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            staff_client.post(
                reverse("recording-ops-order-create", kwargs={"detail_id": detail3.pk}),
                {"expected_revision": 3},
                format="json",
            ).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        stop_after_delete = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail3.pk}),
            {"expected_revision": 3, "order_type": "stop_publish"},
            format="json",
        )
        self.assertEqual(stop_after_delete.status_code, status.HTTP_201_CREATED)

    def test_unlisted_or_needs_review_completion_does_not_leak_youtube_url_and_bad_url_is_400(self):
        staff_client = self._api_client(self.staff_write_key)
        super_client = self._api_client(self.super_write_key)

        item = self._register_item(youtube_draft_video_id="", youtube_draft_privacy="")
        order = staff_client.post(self.order_create_url, {"expected_revision": 1}, format="json")
        self.assertEqual(order.status_code, status.HTTP_201_CREATED)
        super_client.post(self.order_claim_url, {}, format="json")
        complete_url = reverse("recording-ops-order-complete", kwargs={"order_id": order.data["id"]})

        # URL 解析エラーや矛盾した公開状態・動画 ID 欠落の公開完了は 400
        for bad_payload in (
            {"target_revision": 2, "status": "succeeded", "youtube_url": "https://["},
            {"target_revision": 2, "status": "succeeded", "youtube_url": r"https://evil.example\.youtube.com/watch?v=abcdefghijk"},
            {"target_revision": 2, "status": "succeeded", "youtube_url": "https://evil.youtube.com/watch?v=abcdefghijk"},
            {
                "target_revision": 2,
                "status": "succeeded",
                "youtube_draft_privacy": "unlisted",
                "review_status": "published",
            },
            {
                "target_revision": 2,
                "status": "succeeded",
                "youtube_draft_privacy": "unlisted",
                "review_status": "private_fixed",
            },
            {
                "target_revision": 2,
                "status": "succeeded",
                "youtube_draft_privacy": "private",
                "review_status": "unlisted_scheduled",
            },
            {
                "target_revision": 2,
                "status": "succeeded",
                "youtube_draft_privacy": "public",
            },
            {
                "target_revision": 2,
                "status": "succeeded",
                "review_status": "published",
            },
        ):
            self.assertEqual(
                super_client.post(complete_url, bad_payload, format="json").status_code,
                status.HTTP_400_BAD_REQUEST,
                msg=f"Expected 400 for payload={bad_payload}",
            )
        item.refresh_from_db()
        self.assertFalse(item.is_public_locked)

        # 限定公開（needs_review / unlisted_scheduled）では EventDetail.youtube_url を更新しない
        draft_resp = super_client.post(
            complete_url,
            {
                "target_revision": 2,
                "status": "succeeded",
                "youtube_url": "https://youtu.be/dQw4w9WgXcQ",
                "youtube_draft_privacy": "unlisted",
                "review_status": "needs_review",
            },
            format="json",
        )
        self.assertEqual(draft_resp.status_code, status.HTTP_200_OK)
        self.detail.refresh_from_db()
        self.assertIsNone(self.detail.youtube_url)
        item.refresh_from_db()
        self.assertEqual(item.youtube_draft_video_id, "dQw4w9WgXcQ")
        self.assertEqual(item.review_status, RecordingReviewItem.ReviewStatus.NEEDS_REVIEW)

        # 既に youtube_draft_video_id がある項目は、次回の公開確定で result_video_id 省略可
        promote_order = staff_client.post(self.order_create_url, {"expected_revision": 2}, format="json")
        self.assertEqual(promote_order.status_code, status.HTTP_201_CREATED)
        super_client.post(self.order_claim_url, {}, format="json")
        promote_resp = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": promote_order.data["id"]}),
            {"target_revision": 3, "status": "succeeded", "youtube_draft_privacy": "public"},
            format="json",
        )
        self.assertEqual(promote_resp.status_code, status.HTTP_200_OK)
        item.refresh_from_db()
        self.assertEqual(item.review_status, RecordingReviewItem.ReviewStatus.PUBLISHED)
        self.assertTrue(item.is_public_locked)

        # 巨大整数クエリも 400
        self.assertEqual(
            staff_client.get(f"{self.proxy_get_url}?expires_in={'9' * 5000}").status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_stale_running_order_expires_on_create_claim_and_direct_complete(self):
        staff_client = self._api_client(self.staff_write_key)
        super_client = self._api_client(self.super_write_key)

        detail_expired_pub = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item_expired_pub = self._register_item(detail_expired_pub, youtube_draft_video_id="", youtube_draft_privacy="")
        # 1) 回収処理（claim/create）より先に期限切れ running へ直接 complete しても 409 order_lease_expired で拒否しつつ公開履歴と動画 ID は保全する
        direct_order = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail_expired_pub.pk}),
            {"expected_revision": 1},
            format="json",
        )
        direct_order_id = direct_order.data["id"]
        super_client.post(self.order_claim_url, {}, format="json")
        RecordingEditOrder.objects.filter(pk=direct_order_id).update(
            started_at=timezone.now() - timedelta(minutes=31)
        )
        direct_late_complete = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": direct_order_id}),
            {
                "target_revision": 2,
                "status": "succeeded",
                "youtube_url": "https://youtu.be/abcdefghijk",
                "youtube_draft_privacy": "public",
            },
            format="json",
        )
        self.assertEqual(direct_late_complete.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(direct_late_complete.data["code"], "order_lease_expired")
        detail_expired_pub.refresh_from_db()
        self.assertIsNone(detail_expired_pub.youtube_url)
        item_expired_pub.refresh_from_db()
        self.assertTrue(item_expired_pub.is_public_locked)
        self.assertEqual(item_expired_pub.youtube_draft_video_id, "abcdefghijk")
        direct_db = RecordingEditOrder.objects.get(pk=direct_order_id)
        self.assertEqual(direct_db.status, RecordingEditOrder.Status.FAILED)
        self.assertIsNone(direct_db.active_lock_key)

        # 2) 期限切れ running が残っていても新しいオーダー作成時に自動失効しブロックされない
        self._register_item()
        order_resp = staff_client.post(self.order_create_url, {"expected_revision": 1}, format="json")
        order_id = order_resp.data["id"]
        claim_resp = super_client.post(self.order_claim_url, {}, format="json")
        self.assertEqual(claim_resp.data["order"]["id"], order_id)

        RecordingEditOrder.objects.filter(pk=order_id).update(
            started_at=timezone.now() - timedelta(minutes=31)
        )

        stop_resp = staff_client.post(
            self.order_create_url,
            {"expected_revision": 2, "order_type": "stop_publish"},
            format="json",
        )
        self.assertEqual(stop_resp.status_code, status.HTTP_201_CREATED)

        old_order = RecordingEditOrder.objects.get(pk=order_id)
        self.assertEqual(old_order.status, RecordingEditOrder.Status.FAILED)
        self.assertIsNone(old_order.active_lock_key)

        # 旧ワーカーの遅延完了報告は 409 order_not_running で拒否される
        late_complete = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": order_id}),
            {"target_revision": 2, "status": "succeeded"},
            format="json",
        )
        self.assertEqual(late_complete.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(late_complete.data["code"], "order_not_running")

        # 3) claim 呼び出し時も期限切れ running を自動回収して解放し、別の期限切れ running への直 failed 報告も order_lease_expired で拒否
        new_claim = super_client.post(self.order_claim_url, {}, format="json")
        self.assertEqual(new_claim.data["order"]["id"], stop_resp.data["id"])
        RecordingEditOrder.objects.filter(pk=stop_resp.data["id"]).update(
            started_at=timezone.now() - timedelta(minutes=31)
        )
        empty_claim = super_client.post(self.order_claim_url, {}, format="json")
        self.assertIsNone(empty_claim.data["order"])
        stop_order = RecordingEditOrder.objects.get(pk=stop_resp.data["id"])
        self.assertEqual(stop_order.status, RecordingEditOrder.Status.FAILED)
        self.assertIsNone(stop_order.active_lock_key)

        failed_order = staff_client.post(self.order_create_url, {"expected_revision": 3}, format="json")
        super_client.post(self.order_claim_url, {}, format="json")
        RecordingEditOrder.objects.filter(pk=failed_order.data["id"]).update(
            started_at=timezone.now() - timedelta(minutes=31)
        )
        direct_late_fail = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": failed_order.data["id"]}),
            {"target_revision": 4, "status": "failed", "error_message": "timed out"},
            format="json",
        )
        self.assertEqual(direct_late_fail.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(direct_late_fail.data["code"], "order_lease_expired")

    def test_partial_upsert_rejects_contradictory_privacy_status_and_non_11char_video_id(self):
        super_client = self._api_client(self.super_write_key)
        staff_client = self._api_client(self.staff_write_key)

        # 11 文字以外の動画 ID は PUT / complete いずれも 400
        bad_id_put = super_client.put(
            self.upsert_url,
            {
                "proxy_r2_key": self._valid_proxy_key(),
                "proxy_duration_sec": 1200.0,
                "edl_json": _valid_edl(),
                "youtube_draft_video_id": "short1",
            },
            format="json",
        )
        self.assertEqual(bad_id_put.status_code, status.HTTP_400_BAD_REQUEST)

        item = self._register_item(youtube_draft_video_id="dQw4w9WgXcQ", youtube_draft_privacy="private")
        order = staff_client.post(self.order_create_url, {"expected_revision": 1}, format="json")
        self.assertEqual(order.status_code, status.HTTP_201_CREATED)
        super_client.post(self.order_claim_url, {}, format="json")
        complete_url = reverse("recording-ops-order-complete", kwargs={"order_id": order.data["id"]})

        self.assertEqual(
            super_client.post(
                complete_url,
                {"target_revision": 2, "status": "succeeded", "result_video_id": "x"},
                format="json",
            ).status_code,
            status.HTTP_400_BAD_REQUEST,
        )

        # 公開履歴のない private_fixed / private を作成
        done = super_client.post(
            complete_url,
            {
                "target_revision": 2,
                "status": "succeeded",
                "youtube_draft_privacy": "private",
                "review_status": "private_fixed",
            },
            format="json",
        )
        self.assertEqual(done.status_code, status.HTTP_200_OK)
        item.refresh_from_db()
        self.assertFalse(item.is_public_locked)
        self.assertEqual(item.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)

        # youtube_draft_privacy="unlisted" だけの部分 PUT は private_fixed / unlisted 矛盾で 400
        bad_partial = super_client.put(
            self.upsert_url,
            {"expected_revision": 2, "youtube_draft_privacy": "unlisted"},
            format="json",
        )
        self.assertEqual(bad_partial.status_code, status.HTTP_400_BAD_REQUEST)

        # review_status="needs_review" と一緒に更新すれば整合して 200
        ok_pair = super_client.put(
            self.upsert_url,
            {
                "expected_revision": 2,
                "youtube_draft_privacy": "unlisted",
                "review_status": "needs_review",
            },
            format="json",
        )
        self.assertEqual(ok_pair.status_code, status.HTTP_200_OK)
        self.assertEqual(ok_pair.data["review_status"], RecordingReviewItem.ReviewStatus.NEEDS_REVIEW)

    def test_expired_failed_late_report_and_settings_revocation_enqueue_stop_publish(self):
        staff_client = self._api_client(self.staff_write_key)
        super_client = self._api_client(self.super_write_key)

        # 1) 同意撤回後に 30 分超過の遅延完了報告が届いた場合: 409 を返しつつ動画 ID 保全と補償 stop_publish を投入
        item1 = self._register_item(youtube_draft_video_id="", youtube_draft_privacy="")
        o1 = staff_client.post(self.order_create_url, {"expected_revision": 1}, format="json")
        super_client.post(self.order_claim_url, {}, format="json")
        EventDetail.objects.filter(pk=self.detail.pk).update(recording_policy=RecordingPolicy.FORBIDDEN)
        RecordingEditOrder.objects.filter(pk=o1.data["id"]).update(
            started_at=timezone.now() - timedelta(minutes=31)
        )
        late_exp = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": o1.data["id"]}),
            {
                "target_revision": 2,
                "status": "succeeded",
                "result_video_id": "latevid1234",
                "youtube_draft_privacy": "public",
            },
            format="json",
        )
        self.assertEqual(late_exp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(late_exp.data["code"], "order_lease_expired")
        item1.refresh_from_db()
        self.assertEqual(item1.youtube_draft_video_id, "latevid1234")
        self.assertTrue(item1.is_public_locked)
        self.assertTrue(
            RecordingEditOrder.objects.filter(
                review_item=item1,
                order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
                status=RecordingEditOrder.Status.QUEUED,
            ).exists()
        )
        # claim して stop_publish を消化
        c1 = super_client.post(self.order_claim_url, {}, format="json")
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": c1.data["order"]["id"]}),
            {"target_revision": c1.data["order"]["target_revision"], "status": "succeeded"},
            format="json",
        )

        # 2) claim 回収で既に FAILED になったオーダーへの遅延動画 ID 報告でも 409 order_not_running を維持しつつ補償停止
        detail2 = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item2 = self._register_item(detail2, youtube_draft_video_id="", youtube_draft_privacy="")
        o2 = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail2.pk}),
            {"expected_revision": 1},
            format="json",
        )
        super_client.post(self.order_claim_url, {}, format="json")
        RecordingEditOrder.objects.filter(pk=o2.data["id"]).update(
            started_at=timezone.now() - timedelta(minutes=31)
        )
        EventDetail.objects.filter(pk=detail2.pk).update(recording_policy=RecordingPolicy.FORBIDDEN)
        # claim スイープが o2 を自動失敗化
        sweep = super_client.post(self.order_claim_url, {}, format="json")
        self.assertIsNone(sweep.data["order"])
        late_after_sweep = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": o2.data["id"]}),
            {
                "target_revision": 2,
                "status": "succeeded",
                "result_video_id": "sweptvid123",
                "youtube_draft_privacy": "public",
            },
            format="json",
        )
        self.assertEqual(late_after_sweep.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(late_after_sweep.data["code"], "order_not_running")
        item2.refresh_from_db()
        self.assertEqual(item2.youtube_draft_video_id, "sweptvid123")
        self.assertTrue(
            RecordingEditOrder.objects.filter(
                review_item=item2,
                order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
                status=RecordingEditOrder.Status.QUEUED,
            ).exists()
        )
        c2 = super_client.post(self.order_claim_url, {}, format="json")
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": c2.data["order"]["id"]}),
            {"target_revision": c2.data["order"]["target_revision"], "status": "succeeded"},
            format="json",
        )

        # 3) 後続処理失敗（status="failed"）で動画 ID を返した場合も、同意撤回中なら動画 ID を保全し補償停止投入
        detail3 = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item3 = self._register_item(detail3, youtube_draft_video_id="", youtube_draft_privacy="")
        o3 = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail3.pk}),
            {"expected_revision": 1},
            format="json",
        )
        super_client.post(self.order_claim_url, {}, format="json")
        EventDetail.objects.filter(pk=detail3.pk).update(recording_policy=RecordingPolicy.FORBIDDEN)
        partial_fail = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": o3.data["id"]}),
            {
                "target_revision": 2,
                "status": "failed",
                "result_video_id": "failvid1234",
                "youtube_draft_privacy": "public",
                "error_message": "post-publish hook failed",
            },
            format="json",
        )
        self.assertEqual(partial_fail.status_code, status.HTTP_200_OK)
        item3.refresh_from_db()
        self.assertEqual(item3.youtube_draft_video_id, "failvid1234")
        self.assertTrue(item3.is_public_locked)
        self.assertTrue(
            RecordingEditOrder.objects.filter(
                review_item=item3,
                order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
                status=RecordingEditOrder.Status.QUEUED,
            ).exists()
        )
        c3 = super_client.post(self.order_claim_url, {}, format="json")
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": c3.data["order"]["id"]}),
            {"target_revision": c3.data["order"]["target_revision"], "status": "succeeded"},
            format="json",
        )

        # 4) 集会設定画面で撮影許可 OFF（recording_allowed=false）にすると既存公開 URL 除去と stop_publish を自動予約
        detail4 = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item4 = self._register_item(detail4, youtube_draft_video_id="pubvid12345", youtube_draft_privacy="unlisted")
        o4 = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail4.pk}),
            {"expected_revision": 1},
            format="json",
        )
        super_client.post(self.order_claim_url, {}, format="json")
        pub_done = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": o4.data["id"]}),
            {
                "target_revision": 2,
                "status": "succeeded",
                "youtube_url": "https://youtu.be/pubvid12345",
                "youtube_draft_privacy": "public",
            },
            format="json",
        )
        self.assertEqual(pub_done.status_code, status.HTTP_200_OK)
        detail4.refresh_from_db()
        self.assertEqual(detail4.youtube_url, "https://youtu.be/pubvid12345")

        self.client.force_login(self.community_owner)
        settings_resp = self.client.post(
            reverse("community:update_lt_settings", kwargs={"pk": self.community.pk}),
            {
                "accepts_lt_application": "on",
                "recording_allowed": "false",
                "default_lt_duration": "30",
                "lt_start_offset_minutes": "30",
            },
        )
        self.assertEqual(settings_resp.status_code, status.HTTP_302_FOUND)
        detail4.refresh_from_db()
        self.assertIsNone(detail4.youtube_url)
        self.assertTrue(
            RecordingEditOrder.objects.filter(
                review_item=item4,
                order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
                status=RecordingEditOrder.Status.QUEUED,
            ).exists()
        )
        c4 = super_client.post(self.order_claim_url, {}, format="json")
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": c4.data["order"]["id"]}),
            {"target_revision": c4.data["order"]["target_revision"], "status": "succeeded"},
            format="json",
        )

    def test_multi_video_stop_isolation_bulk_delete_move_community_and_cascade_preservation(self):
        staff_client = self._api_client(self.staff_write_key)
        super_client = self._api_client(self.super_write_key)

        # 1) 停止処理中の遅延別動画報告: videoB を stop_publish 中に旧オーダーから videoA が届いても、A が private_fixed 扱いにならず別 stop_publish が積まれる
        detail_ab = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item_ab = self._register_item(detail_ab, youtube_draft_video_id="", youtube_draft_privacy="")
        old_a = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail_ab.pk}),
            {"expected_revision": 1},
            format="json",
        )
        super_client.post(self.order_claim_url, {}, format="json")
        RecordingEditOrder.objects.filter(pk=old_a.data["id"]).update(
            started_at=timezone.now() - timedelta(minutes=31)
        )
        # 別オーダーを作成（old_a は自動失効）して videoB を公開
        ord_b = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail_ab.pk}),
            {"expected_revision": 2},
            format="json",
        )
        super_client.post(self.order_claim_url, {}, format="json")
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": ord_b.data["id"]}),
            {
                "target_revision": 3,
                "status": "succeeded",
                "result_video_id": "videoB12345",
                "youtube_url": "https://youtu.be/videoB12345",
                "youtube_draft_privacy": "public",
            },
            format="json",
        )
        # videoB の stop_publish を作成して claim 済みの間に、旧 old_a から videoA の遅延報告が来る
        stop_b = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail_ab.pk}),
            {"expected_revision": 3, "order_type": "stop_publish"},
            format="json",
        )
        claim_b = super_client.post(self.order_claim_url, {}, format="json")
        self.assertEqual(claim_b.data["order"]["target_video_id"], "videoB12345")

        late_a = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": old_a.data["id"]}),
            {
                "target_revision": 2,
                "status": "succeeded",
                "result_video_id": "videoA12345",
                "youtube_draft_privacy": "public",
            },
            format="json",
        )
        self.assertEqual(late_a.status_code, status.HTTP_409_CONFLICT)

        # stop_b が完了しても videoA が残っているため private_fixed にならず videoA の stop_publish が追加キュー投入される
        done_b = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": stop_b.data["id"]}),
            {
                "target_revision": stop_b.data["target_revision"],
                "status": "succeeded",
                "result_video_id": "videoB12345",
            },
            format="json",
        )
        self.assertEqual(done_b.status_code, status.HTTP_200_OK)
        item_ab.refresh_from_db()
        self.assertNotEqual(item_ab.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)
        claim_a = super_client.post(self.order_claim_url, {}, format="json")
        self.assertIsNotNone(claim_a.data["order"])
        self.assertEqual(claim_a.data["order"]["order_type"], RecordingEditOrder.OrderType.STOP_PUBLISH)
        self.assertEqual(claim_a.data["order"]["target_video_id"], "videoA12345")
        done_a = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": claim_a.data["order"]["id"]}),
            {
                "target_revision": claim_a.data["order"]["target_revision"],
                "status": "succeeded",
                "result_video_id": "videoA12345",
            },
            format="json",
        )
        self.assertEqual(done_a.status_code, status.HTTP_200_OK)
        item_ab.refresh_from_db()
        self.assertEqual(item_ab.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)
        self.assertEqual(item_ab.pending_stop_video_ids, [])

        # 2) 同意有効中の failed 報告でも public 指定なら公開済みロックが付き上書き不可になる
        detail_fail_pub = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item_fp = self._register_item(detail_fail_pub, youtube_draft_video_id="", youtube_draft_privacy="")
        ord_fp = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail_fail_pub.pk}),
            {"expected_revision": 1},
            format="json",
        )
        super_client.post(self.order_claim_url, {}, format="json")
        fail_pub_resp = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": ord_fp.data["id"]}),
            {
                "target_revision": 2,
                "status": "failed",
                "result_video_id": "pubfail1234",
                "youtube_draft_privacy": "public",
                "error_message": "notify failed after publish",
            },
            format="json",
        )
        self.assertEqual(fail_pub_resp.status_code, status.HTTP_200_OK)
        item_fp.refresh_from_db()
        self.assertTrue(item_fp.is_public_locked)
        self.assertEqual(item_fp.youtube_draft_video_id, "pubfail1234")

        # 3) 非許可集会への所属変更で自動停止予約される
        detail_move = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item_move = self._register_item(detail_move, youtube_draft_video_id="movevid1234", youtube_draft_privacy="unlisted")
        detail_move.youtube_url = "https://youtu.be/movevid1234"
        detail_move.save(update_fields=["youtube_url"])
        detail_move.event = self.disallowed_event
        detail_move.save(update_fields=["event"])
        detail_move.refresh_from_db()
        self.assertIsNone(detail_move.youtube_url)
        self.assertTrue(
            RecordingEditOrder.objects.filter(
                review_item=item_move,
                order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
                status=RecordingEditOrder.Status.QUEUED,
            ).exists()
        )
        cm = super_client.post(self.order_claim_url, {}, format="json")
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": cm.data["order"]["id"]}),
            {"target_revision": cm.data["order"]["target_revision"], "status": "succeeded"},
            format="json",
        )

        # 4) QuerySet 一括 delete()（EventDetailQuerySet.delete）でも停止予約される
        detail_bulk = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item_bulk = self._register_item(detail_bulk, youtube_draft_video_id="bulkvid1234", youtube_draft_privacy="unlisted")
        EventDetail.objects.filter(pk=detail_bulk.pk).delete()
        self.assertTrue(
            RecordingEditOrder.objects.filter(
                review_item=item_bulk,
                order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
                status=RecordingEditOrder.Status.QUEUED,
            ).exists()
        )
        cb = super_client.post(self.order_claim_url, {}, format="json")
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": cb.data["order"]["id"]}),
            {"target_revision": cb.data["order"]["target_revision"], "status": "succeeded"},
            format="json",
        )

        # 5) 親イベントの物理削除（delete_event_with_tombstones -> event.delete()）でも動画 ID と stop_publish が保持され、claim/complete できる
        ephemeral_event = make_event(self.community, event_date=timezone.localdate() + timedelta(days=14))
        detail_cas = make_event_detail(ephemeral_event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item_cas = self._register_item(detail_cas, youtube_draft_video_id="cascad12345", youtube_draft_privacy="unlisted")
        delete_event_with_tombstones(ephemeral_event)
        item_cas.refresh_from_db()
        self.assertIsNone(item_cas.event_detail_id)
        self.assertEqual(item_cas.youtube_draft_video_id, "cascad12345")
        claim_cas = super_client.post(self.order_claim_url, {}, format="json")
        self.assertIsNotNone(claim_cas.data["order"])
        self.assertEqual(claim_cas.data["order"]["order_type"], RecordingEditOrder.OrderType.STOP_PUBLISH)
        self.assertIsNone(claim_cas.data["order"]["event_detail_id"])
        self.assertEqual(claim_cas.data["order"]["target_video_id"], "cascad12345")
        done_cas = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": claim_cas.data["order"]["id"]}),
            {"target_revision": claim_cas.data["order"]["target_revision"], "status": "succeeded"},
            format="json",
        )
        self.assertEqual(done_cas.status_code, status.HTTP_200_OK)
        item_cas.refresh_from_db()
        self.assertEqual(item_cas.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)

        # 6) apply_recording_opt_in コマンドの一括 UPDATE でも撮影許可 OFF にされた集会の動画へ stop_publish を積む
        opt_community = make_community(name="一括停止テスト集会", owner=self.community_owner, recording_allowed=True)
        opt_event = make_event(opt_community)
        detail_opt = make_event_detail(opt_event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item_opt = self._register_item(detail_opt, youtube_draft_video_id="optinv12345", youtube_draft_privacy="unlisted")
        call_command("apply_recording_opt_in", "--keep-community-id", str(self.community.pk))
        self.assertTrue(
            RecordingEditOrder.objects.filter(
                review_item=item_opt,
                order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
                status=RecordingEditOrder.Status.QUEUED,
            ).exists()
        )
        c_opt = super_client.post(self.order_claim_url, {}, format="json")
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": c_opt.data["order"]["id"]}),
            {"target_revision": c_opt.data["order"]["target_revision"], "status": "succeeded"},
            format="json",
        )

        # 7) stop_publish の完了報告で対象動画と異なる動画 ID（または URL）は 400 拒否し、停止待ちを消さない
        detail_mis = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item_mis = self._register_item(detail_mis, youtube_draft_video_id="targetA1234", youtube_draft_privacy="unlisted")
        EventDetail.objects.filter(pk=detail_mis.pk).update(recording_policy=RecordingPolicy.FORBIDDEN)
        stop_mis = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail_mis.pk}),
            {"expected_revision": 1, "order_type": "stop_publish"},
            format="json",
        )
        self.assertEqual(stop_mis.status_code, status.HTTP_201_CREATED)
        claim_mis = super_client.post(self.order_claim_url, {}, format="json")
        self.assertEqual(claim_mis.data["order"]["target_video_id"], "targetA1234")
        bad_stop_complete = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": stop_mis.data["id"]}),
            {
                "target_revision": stop_mis.data["target_revision"],
                "status": "succeeded",
                "result_video_id": "otherB12345",
            },
            format="json",
        )
        self.assertEqual(bad_stop_complete.status_code, status.HTTP_400_BAD_REQUEST)
        ok_stop_complete = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": stop_mis.data["id"]}),
            {
                "target_revision": stop_mis.data["target_revision"],
                "status": "succeeded",
                "result_video_id": "targetA1234",
            },
            format="json",
        )
        self.assertEqual(ok_stop_complete.status_code, status.HTTP_200_OK)
        item_mis.refresh_from_db()
        self.assertEqual(item_mis.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)

        # 8) 同意有効中の遅延別動画報告は旧未停止動画も両方保全し、後から同意撤回で全件停止できる
        detail_both = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item_both = self._register_item(detail_both, youtube_draft_video_id="", youtube_draft_privacy="")
        old_both_a = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail_both.pk}),
            {"expected_revision": 1},
            format="json",
        )
        super_client.post(self.order_claim_url, {}, format="json")
        RecordingEditOrder.objects.filter(pk=old_both_a.data["id"]).update(
            started_at=timezone.now() - timedelta(minutes=31)
        )
        ord_both_b = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail_both.pk}),
            {"expected_revision": 2},
            format="json",
        )
        super_client.post(self.order_claim_url, {}, format="json")
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": ord_both_b.data["id"]}),
            {
                "target_revision": 3,
                "status": "succeeded",
                "result_video_id": "videoB22222",
                "youtube_draft_privacy": "public",
            },
            format="json",
        )
        # 同意有効中に old_both_a から別動画 A が遅延到着
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": old_both_a.data["id"]}),
            {
                "target_revision": 2,
                "status": "succeeded",
                "result_video_id": "videoA11111",
                "youtube_draft_privacy": "public",
            },
            format="json",
        )
        item_both.refresh_from_db()
        self.assertIn("videoB22222", item_both.pending_stop_video_ids)
        self.assertIn("videoA11111", item_both.pending_stop_video_ids)

        # 後から同意撤回すると、両方とも順次 stop_publish されるまで private_fixed にならない
        detail_both.recording_policy = RecordingPolicy.FORBIDDEN
        detail_both.save(update_fields=["recording_policy"])
        stop_first = super_client.post(self.order_claim_url, {}, format="json")
        first_vid = stop_first.data["order"]["target_video_id"]
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": stop_first.data["order"]["id"]}),
            {"target_revision": stop_first.data["order"]["target_revision"], "status": "succeeded", "result_video_id": first_vid},
            format="json",
        )
        item_both.refresh_from_db()
        self.assertNotEqual(item_both.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)
        stop_second = super_client.post(self.order_claim_url, {}, format="json")
        second_vid = stop_second.data["order"]["target_video_id"]
        self.assertNotEqual(first_vid, second_vid)
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": stop_second.data["order"]["id"]}),
            {"target_revision": stop_second.data["order"]["target_revision"], "status": "succeeded", "result_video_id": second_vid},
            format="json",
        )
        item_both.refresh_from_db()
        self.assertEqual(item_both.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)
        self.assertEqual(item_both.pending_stop_video_ids, [])

        # 9) 動画 ID 未取得の failed public 報告でも公開ロックが付く + 親 Event の community 変更で停止予約される
        detail_novid = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item_novid = self._register_item(detail_novid, youtube_draft_video_id="", youtube_draft_privacy="")
        ord_nv = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail_novid.pk}),
            {"expected_revision": 1},
            format="json",
        )
        super_client.post(self.order_claim_url, {}, format="json")
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": ord_nv.data["id"]}),
            {
                "target_revision": 2,
                "status": "failed",
                "youtube_draft_privacy": "public",
                "error_message": "public without id",
            },
            format="json",
        )
        item_novid.refresh_from_db()
        self.assertTrue(item_novid.is_public_locked)

        move_event = make_event(self.community, event_date=timezone.localdate() + timedelta(days=21))
        detail_ev_move = make_event_detail(move_event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item_ev_move = self._register_item(detail_ev_move, youtube_draft_video_id="evmove12345", youtube_draft_privacy="unlisted")
        move_event.community = self.disallowed_community
        move_event.save(update_fields=["community"])
        self.assertTrue(
            RecordingEditOrder.objects.filter(
                review_item=item_ev_move,
                order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
                status=RecordingEditOrder.Status.QUEUED,
            ).exists()
        )

    def test_pending_stop_prevents_private_fixed_and_allows_manual_stop_publish(self):
        staff_client = self._api_client(self.staff_write_key)
        super_client = self._api_client(self.super_write_key)

        # 1) unlisted 動画 A の状態で同意撤回＋進行中の別動画 B が private で完了しても、
        # private_fixed にならず動画 A 向けの compensating stop_publish が積まれてから解決される
        detail_a = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item_a = self._register_item(
            detail_a,
            youtube_draft_video_id="videoA99999",
            youtube_draft_privacy="unlisted",
        )
        ord_b = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail_a.pk}),
            {"expected_revision": 1},
            format="json",
        )
        self.assertEqual(ord_b.status_code, status.HTTP_201_CREATED)
        claim_b = super_client.post(self.order_claim_url, {}, format="json")
        self.assertEqual(claim_b.data["order"]["id"], ord_b.data["id"])

        detail_a.recording_policy = RecordingPolicy.FORBIDDEN
        detail_a.save(update_fields=["recording_policy"])

        complete_b = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": ord_b.data["id"]}),
            {
                "target_revision": ord_b.data["target_revision"],
                "status": "succeeded",
                "result_video_id": "videoB88888",
                "youtube_draft_privacy": "private",
            },
            format="json",
        )
        self.assertEqual(complete_b.status_code, status.HTTP_200_OK)
        item_a.refresh_from_db()
        self.assertNotEqual(item_a.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)
        self.assertIn("videoA99999", item_a.pending_stop_video_ids)
        self.assertEqual(item_a.youtube_draft_video_id, "videoA99999")
        self.assertEqual(item_a.youtube_draft_privacy, "")

        claim_stop_a = super_client.post(self.order_claim_url, {}, format="json")
        self.assertIsNotNone(claim_stop_a.data["order"])
        self.assertEqual(
            claim_stop_a.data["order"]["order_type"],
            RecordingEditOrder.OrderType.STOP_PUBLISH,
        )
        self.assertEqual(claim_stop_a.data["order"]["target_video_id"], "videoA99999")
        complete_stop_a = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": claim_stop_a.data["order"]["id"]}),
            {
                "target_revision": claim_stop_a.data["order"]["target_revision"],
                "status": "succeeded",
                "result_video_id": "videoA99999",
            },
            format="json",
        )
        self.assertEqual(complete_stop_a.status_code, status.HTTP_200_OK)
        item_a.refresh_from_db()
        self.assertEqual(item_a.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)
        self.assertEqual(item_a.pending_stop_video_ids, [])

        # 2) PUT で youtube_draft_video_id を空に戻した場合も pending_stop_video_ids に残るため、
        # 同意撤回後・論理削除後の手動 stop_publish が 403/404 ではなく 201 で作成できる
        detail_m = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item_m = self._register_item(
            detail_m,
            youtube_draft_video_id="manualA1234",
            youtube_draft_privacy="unlisted",
        )
        put_clear = super_client.put(
            reverse("recording-ops-item-upsert", kwargs={"detail_id": detail_m.pk}),
            {
                "expected_revision": item_m.edl_revision,
                "review_status": "needs_review",
                "youtube_draft_video_id": "",
                "youtube_draft_privacy": "",
            },
            format="json",
        )
        self.assertEqual(put_clear.status_code, status.HTTP_200_OK)
        item_m.refresh_from_db()
        self.assertEqual(item_m.pending_stop_video_ids, ["manualA1234"])

        detail_m.recording_policy = RecordingPolicy.FORBIDDEN
        detail_m.save(update_fields=["recording_policy"])
        claim_auto = super_client.post(self.order_claim_url, {}, format="json")
        self.assertEqual(claim_auto.data["order"]["target_video_id"], "manualA1234")
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": claim_auto.data["order"]["id"]}),
            {
                "target_revision": claim_auto.data["order"]["target_revision"],
                "status": "failed",
                "error_message": "temporary error",
            },
            format="json",
        )
        item_m.refresh_from_db()
        retry_after_revoke = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail_m.pk}),
            {
                "order_type": RecordingEditOrder.OrderType.STOP_PUBLISH,
                "expected_revision": item_m.edl_revision,
            },
            format="json",
        )
        self.assertEqual(retry_after_revoke.status_code, status.HTTP_201_CREATED)
        self.assertEqual(retry_after_revoke.data["target_video_id"], "manualA1234")

        claim_fail2 = super_client.post(self.order_claim_url, {}, format="json")
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": claim_fail2.data["order"]["id"]}),
            {
                "target_revision": claim_fail2.data["order"]["target_revision"],
                "status": "failed",
                "error_message": "second error",
            },
            format="json",
        )
        detail_m.deleted_at = timezone.now()
        detail_m.save(update_fields=["deleted_at"])
        claim_del_auto = super_client.post(self.order_claim_url, {}, format="json")
        self.assertEqual(claim_del_auto.data["order"]["target_video_id"], "manualA1234")
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": claim_del_auto.data["order"]["id"]}),
            {
                "target_revision": claim_del_auto.data["order"]["target_revision"],
                "status": "failed",
                "error_message": "third error",
            },
            format="json",
        )
        item_m.refresh_from_db()
        retry_after_delete = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail_m.pk}),
            {
                "order_type": RecordingEditOrder.OrderType.STOP_PUBLISH,
                "expected_revision": item_m.edl_revision,
            },
            format="json",
        )
        self.assertEqual(retry_after_delete.status_code, status.HTTP_201_CREATED)
        self.assertEqual(retry_after_delete.data["target_video_id"], "manualA1234")
        claim_m_end = super_client.post(self.order_claim_url, {}, format="json")
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": claim_m_end.data["order"]["id"]}),
            {
                "target_revision": claim_m_end.data["order"]["target_revision"],
                "status": "succeeded",
                "result_video_id": "manualA1234",
            },
            format="json",
        )

        # 3) 同意有効のまま unlisted 動画 A を保持しつつ別動画 B が private で完了した場合も、
        # 自動的に動画 A 向けの補償 stop_publish が積まれ、完了後に private_fixed へ収束する
        detail_v = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item_v = self._register_item(
            detail_v,
            youtube_draft_video_id="validA12345",
            youtube_draft_privacy="unlisted",
        )
        ord_v = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail_v.pk}),
            {"expected_revision": 1},
            format="json",
        )
        super_client.post(self.order_claim_url, {}, format="json")
        done_v = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": ord_v.data["id"]}),
            {
                "target_revision": ord_v.data["target_revision"],
                "status": "succeeded",
                "result_video_id": "validB54321",
                "youtube_draft_privacy": "private",
            },
            format="json",
        )
        self.assertEqual(done_v.status_code, status.HTTP_200_OK)
        item_v.refresh_from_db()
        self.assertNotEqual(item_v.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)
        self.assertEqual(item_v.pending_stop_video_ids, ["validA12345"])

        claim_valid_stop = super_client.post(self.order_claim_url, {}, format="json")
        self.assertIsNotNone(claim_valid_stop.data["order"])
        self.assertEqual(
            claim_valid_stop.data["order"]["order_type"],
            RecordingEditOrder.OrderType.STOP_PUBLISH,
        )
        self.assertEqual(claim_valid_stop.data["order"]["target_video_id"], "validA12345")
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": claim_valid_stop.data["order"]["id"]}),
            {
                "target_revision": claim_valid_stop.data["order"]["target_revision"],
                "status": "succeeded",
                "result_video_id": "validA12345",
            },
            format="json",
        )
        item_v.refresh_from_db()
        self.assertEqual(item_v.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)
        self.assertEqual(item_v.pending_stop_video_ids, [])

    def test_physical_delete_stop_publish_failure_and_lease_expiry_retries(self):
        staff_client = self._api_client(self.staff_write_key)
        super_client = self._api_client(self.super_write_key)

        ephemeral_event = make_event(self.community, event_date=timezone.localdate() + timedelta(days=30))
        detail_phys = make_event_detail(ephemeral_event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item_phys = self._register_item(
            detail_phys,
            youtube_draft_video_id="physdel1234",
            youtube_draft_privacy="unlisted",
        )
        delete_event_with_tombstones(ephemeral_event)
        item_phys.refresh_from_db()
        self.assertIsNone(item_phys.event_detail_id)

        retry_url = reverse("recording-ops-review-item-stop-publish", kwargs={"review_item_id": item_phys.pk})

        # 経路 1: 物理削除後の stop_publish が worker failed で終了しても review_item_id 指定で再試行可能
        c1 = super_client.post(self.order_claim_url, {}, format="json")
        self.assertEqual(c1.data["order"]["order_type"], RecordingEditOrder.OrderType.STOP_PUBLISH)
        f1 = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": c1.data["order"]["id"]}),
            {
                "target_revision": c1.data["order"]["target_revision"],
                "status": "failed",
                "error_message": "youtube temporary error",
            },
            format="json",
        )
        self.assertEqual(f1.status_code, status.HTTP_200_OK)
        self.assertEqual(
            staff_client.post(
                reverse("recording-ops-order-create", kwargs={"detail_id": detail_phys.pk}),
                {"order_type": RecordingEditOrder.OrderType.STOP_PUBLISH, "expected_revision": 1},
                format="json",
            ).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        item_phys.refresh_from_db()
        r1 = staff_client.post(retry_url, {"expected_revision": item_phys.edl_revision}, format="json")
        self.assertEqual(r1.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(r1.data["event_detail_id"])
        self.assertEqual(r1.data["target_video_id"], "physdel1234")

        # 経路 2: 物理削除後の stop_publish が running のままリース失効→ claim スイープ後に再試行可能
        c2 = super_client.post(self.order_claim_url, {}, format="json")
        RecordingEditOrder.objects.filter(pk=c2.data["order"]["id"]).update(
            started_at=timezone.now() - timedelta(minutes=31)
        )
        sweep = super_client.post(self.order_claim_url, {}, format="json")
        self.assertIsNone(sweep.data["order"])
        item_phys.refresh_from_db()
        r2 = staff_client.post(retry_url, {"expected_revision": item_phys.edl_revision}, format="json")
        self.assertEqual(r2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(r2.data["target_video_id"], "physdel1234")

        # 経路 3: 物理削除後の stop_publish がリース超過後に遅延 complete（409 order_lease_expired）を受け取っても再試行し収束可能
        c3 = super_client.post(self.order_claim_url, {}, format="json")
        RecordingEditOrder.objects.filter(pk=c3.data["order"]["id"]).update(
            started_at=timezone.now() - timedelta(minutes=31)
        )
        late = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": c3.data["order"]["id"]}),
            {"target_revision": c3.data["order"]["target_revision"], "status": "succeeded"},
            format="json",
        )
        self.assertEqual(late.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(late.data["code"], "order_lease_expired")
        item_phys.refresh_from_db()
        r3 = staff_client.post(retry_url, {"expected_revision": item_phys.edl_revision}, format="json")
        self.assertEqual(r3.status_code, status.HTTP_201_CREATED)

        c4 = super_client.post(self.order_claim_url, {}, format="json")
        done = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": c4.data["order"]["id"]}),
            {
                "target_revision": c4.data["order"]["target_revision"],
                "status": "succeeded",
                "result_video_id": "physdel1234",
            },
            format="json",
        )
        self.assertEqual(done.status_code, status.HTTP_200_OK)
        item_phys.refresh_from_db()
        self.assertEqual(item_phys.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)
        self.assertEqual(item_phys.pending_stop_video_ids, [])

        # 孤立した未公開項目（動画なし・公開状態なし）は 409 nothing_to_stop、存在しない ID は 404
        empty_event = make_event(self.community, event_date=timezone.localdate() + timedelta(days=31))
        empty_detail = make_event_detail(empty_event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        empty_item = self._register_item(empty_detail, youtube_draft_video_id="", youtube_draft_privacy="")
        delete_event_with_tombstones(empty_event)
        empty_item.refresh_from_db()
        nothing = staff_client.post(
            reverse("recording-ops-review-item-stop-publish", kwargs={"review_item_id": empty_item.pk}),
            {"expected_revision": empty_item.edl_revision},
            format="json",
        )
        self.assertEqual(nothing.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(nothing.data["code"], "nothing_to_stop")
        missing = staff_client.post(
            reverse("recording-ops-review-item-stop-publish", kwargs={"review_item_id": 9999999}),
            {"expected_revision": 1},
            format="json",
        )
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)

    def test_late_public_after_stop_and_new_video_without_privacy_trigger_compensating_stop(self):
        staff_client = self._api_client(self.staff_write_key)
        super_client = self._api_client(self.super_write_key)

        # 1) 同意有効のまま手動 stop_publish が完了した後、先行して失効した apply_edl から
        # 遅延で public 完了報告が届いても補償 stop_publish が投入される
        detail_late = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item_late = self._register_item(detail_late, youtube_draft_video_id="", youtube_draft_privacy="")
        old_ord = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail_late.pk}),
            {"expected_revision": 1},
            format="json",
        )
        super_client.post(self.order_claim_url, {}, format="json")
        RecordingEditOrder.objects.filter(pk=old_ord.data["id"]).update(
            started_at=timezone.now() - timedelta(minutes=31)
        )
        ord_b = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail_late.pk}),
            {"expected_revision": 2},
            format="json",
        )
        super_client.post(self.order_claim_url, {}, format="json")
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": ord_b.data["id"]}),
            {
                "target_revision": ord_b.data["target_revision"],
                "status": "succeeded",
                "result_video_id": "newpubB1234",
                "youtube_draft_privacy": "public",
            },
            format="json",
        )
        item_late.refresh_from_db()
        stop_ord = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail_late.pk}),
            {"order_type": RecordingEditOrder.OrderType.STOP_PUBLISH, "expected_revision": item_late.edl_revision},
            format="json",
        )
        self.assertEqual(stop_ord.status_code, status.HTTP_201_CREATED)
        super_client.post(self.order_claim_url, {}, format="json")
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": stop_ord.data["id"]}),
            {"target_revision": stop_ord.data["target_revision"], "status": "succeeded", "result_video_id": "newpubB1234"},
            format="json",
        )
        item_late.refresh_from_db()
        self.assertEqual(item_late.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)

        late_resp = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": old_ord.data["id"]}),
            {
                "target_revision": old_ord.data["target_revision"],
                "status": "succeeded",
                "result_video_id": "latepubA123",
                "youtube_draft_privacy": "public",
                "review_status": "published",
            },
            format="json",
        )
        self.assertEqual(late_resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(late_resp.data["code"], "order_not_running")
        claim_comp = super_client.post(self.order_claim_url, {}, format="json")
        self.assertIsNotNone(claim_comp.data["order"])
        self.assertEqual(claim_comp.data["order"]["order_type"], RecordingEditOrder.OrderType.STOP_PUBLISH)
        self.assertEqual(claim_comp.data["order"]["target_video_id"], "latepubA123")
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": claim_comp.data["order"]["id"]}),
            {"target_revision": claim_comp.data["order"]["target_revision"], "status": "succeeded", "result_video_id": "latepubA123"},
            format="json",
        )
        item_late.refresh_from_db()
        self.assertEqual(item_late.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)
        self.assertEqual(item_late.pending_stop_video_ids, [])

        # 2) 動画 A が private_fixed/private の状態で再編集→実行中に同意撤回→別動画 B を privacy 省略で完了しても、
        # A の private を引き継がず、B を停止待ちに入れて補償 stop_publish を発行する
        detail_priv = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item_priv = self._register_item(
            detail_priv,
            review_status=RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
            youtube_draft_video_id="oldprivA123",
            youtube_draft_privacy="private",
        )
        item_priv.review_status = RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
        item_priv.save(update_fields=["review_status"])

        ord_new_b = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail_priv.pk}),
            {"expected_revision": item_priv.edl_revision},
            format="json",
        )
        self.assertEqual(ord_new_b.status_code, status.HTTP_201_CREATED)
        super_client.post(self.order_claim_url, {}, format="json")

        detail_priv.recording_policy = RecordingPolicy.FORBIDDEN
        detail_priv.save(update_fields=["recording_policy"])

        done_new_b = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": ord_new_b.data["id"]}),
            {
                "target_revision": ord_new_b.data["target_revision"],
                "status": "succeeded",
                "result_video_id": "newunkB1234",
            },
            format="json",
        )
        self.assertEqual(done_new_b.status_code, status.HTTP_200_OK)
        item_priv.refresh_from_db()
        self.assertNotEqual(item_priv.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)
        self.assertEqual(item_priv.youtube_draft_privacy, "")
        self.assertEqual(item_priv.pending_stop_video_ids, ["newunkB1234"])

        claim_stop_b = super_client.post(self.order_claim_url, {}, format="json")
        self.assertIsNotNone(claim_stop_b.data["order"])
        self.assertEqual(claim_stop_b.data["order"]["order_type"], RecordingEditOrder.OrderType.STOP_PUBLISH)
        self.assertEqual(claim_stop_b.data["order"]["target_video_id"], "newunkB1234")
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": claim_stop_b.data["order"]["id"]}),
            {"target_revision": claim_stop_b.data["order"]["target_revision"], "status": "succeeded", "result_video_id": "newunkB1234"},
            format="json",
        )
        item_priv.refresh_from_db()
        self.assertEqual(item_priv.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)
        self.assertEqual(item_priv.pending_stop_video_ids, [])

    def test_put_video_change_and_deferred_stop_resume_across_order_outcomes(self):
        staff_client = self._api_client(self.staff_write_key)
        super_client = self._api_client(self.super_write_key)

        # 1a) private_fixed/private の動画 A に対し PUT で別動画 B の ID だけ渡しても private_fixed/private を継承しない
        detail_put1 = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item_put1 = self._register_item(
            detail_put1,
            review_status=RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
            youtube_draft_video_id="putoldA1234",
            youtube_draft_privacy="private",
        )
        item_put1.review_status = RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
        item_put1.save(update_fields=["review_status"])
        put_new = super_client.put(
            reverse("recording-ops-item-upsert", kwargs={"detail_id": detail_put1.pk}),
            {"expected_revision": item_put1.edl_revision, "youtube_draft_video_id": "putnewB1234"},
            format="json",
        )
        self.assertEqual(put_new.status_code, status.HTTP_200_OK)
        item_put1.refresh_from_db()
        self.assertEqual(item_put1.review_status, RecordingReviewItem.ReviewStatus.NEEDS_REVIEW)
        self.assertEqual(item_put1.youtube_draft_privacy, "")
        detail_put1.recording_policy = RecordingPolicy.FORBIDDEN
        detail_put1.save(update_fields=["recording_policy"])
        c_put1 = super_client.post(self.order_claim_url, {}, format="json")
        self.assertIsNotNone(c_put1.data["order"])
        self.assertEqual(c_put1.data["order"]["order_type"], RecordingEditOrder.OrderType.STOP_PUBLISH)
        self.assertEqual(c_put1.data["order"]["target_video_id"], "putnewB1234")
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": c_put1.data["order"]["id"]}),
            {"target_revision": c_put1.data["order"]["target_revision"], "status": "succeeded", "result_video_id": "putnewB1234"},
            format="json",
        )

        # 1b) PUT で review_status=needs_review のみ指定しても旧 private は消え、続く complete でも非公開確定にならない
        detail_put2 = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
        item_put2 = self._register_item(
            detail_put2,
            review_status=RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
            youtube_draft_video_id="putnrA12345",
            youtube_draft_privacy="private",
        )
        item_put2.review_status = RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
        item_put2.save(update_fields=["review_status"])
        put_nr = super_client.put(
            reverse("recording-ops-item-upsert", kwargs={"detail_id": detail_put2.pk}),
            {"expected_revision": item_put2.edl_revision, "review_status": "needs_review"},
            format="json",
        )
        self.assertEqual(put_nr.status_code, status.HTTP_200_OK)
        item_put2.refresh_from_db()
        self.assertEqual(item_put2.youtube_draft_privacy, "")
        ord_put2 = staff_client.post(
            reverse("recording-ops-order-create", kwargs={"detail_id": detail_put2.pk}),
            {"expected_revision": item_put2.edl_revision},
            format="json",
        )
        super_client.post(self.order_claim_url, {}, format="json")
        done_put2 = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": ord_put2.data["id"]}),
            {"target_revision": ord_put2.data["target_revision"], "status": "succeeded"},
            format="json",
        )
        self.assertEqual(done_put2.status_code, status.HTTP_200_OK)
        item_put2.refresh_from_db()
        self.assertNotEqual(item_put2.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)
        detail_put2.recording_policy = RecordingPolicy.FORBIDDEN
        detail_put2.save(update_fields=["recording_policy"])
        c_put2 = super_client.post(self.order_claim_url, {}, format="json")
        self.assertEqual(c_put2.data["order"]["order_type"], RecordingEditOrder.OrderType.STOP_PUBLISH)
        super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": c_put2.data["order"]["id"]}),
            {"target_revision": c_put2.data["order"]["target_revision"], "status": "succeeded", "result_video_id": "putnrA12345"},
            format="json",
        )

        # 2) stop_publish 完了後・後続 apply_edl C 実行中に旧指示から遅延 public 報告が届いて保留された場合、
        # C が unlisted 成功 / failed / claim リース失効 / 遅延 complete 失効 / 新規オーダー作成時スイープ /
        # 期限切れキャンセルのどの経路で終わっても補償停止が再開される
        for idx, outcome in enumerate(
            ("unlisted_succeed", "failed", "claim_sweep", "late_expire", "create_sweep", "claim_proxy_expired")
        ):
            detail_d = make_event_detail(self.event, status="approved", recording_policy=RecordingPolicy.PUBLIC)
            item_d = self._register_item(detail_d, youtube_draft_video_id="", youtube_draft_privacy="")
            old_d = staff_client.post(
                reverse("recording-ops-order-create", kwargs={"detail_id": detail_d.pk}),
                {"expected_revision": 1},
                format="json",
            )
            super_client.post(self.order_claim_url, {}, format="json")
            RecordingEditOrder.objects.filter(pk=old_d.data["id"]).update(
                started_at=timezone.now() - timedelta(minutes=31)
            )
            b_vid = f"midvidB000{idx}"
            ord_b = staff_client.post(
                reverse("recording-ops-order-create", kwargs={"detail_id": detail_d.pk}),
                {"expected_revision": 2},
                format="json",
            )
            super_client.post(self.order_claim_url, {}, format="json")
            super_client.post(
                reverse("recording-ops-order-complete", kwargs={"order_id": ord_b.data["id"]}),
                {
                    "target_revision": ord_b.data["target_revision"],
                    "status": "succeeded",
                    "result_video_id": b_vid,
                    "youtube_draft_privacy": "unlisted",
                },
                format="json",
            )
            item_d.refresh_from_db()
            stop_b = staff_client.post(
                reverse("recording-ops-order-create", kwargs={"detail_id": detail_d.pk}),
                {"order_type": RecordingEditOrder.OrderType.STOP_PUBLISH, "expected_revision": item_d.edl_revision},
                format="json",
            )
            super_client.post(self.order_claim_url, {}, format="json")
            super_client.post(
                reverse("recording-ops-order-complete", kwargs={"order_id": stop_b.data["id"]}),
                {"target_revision": stop_b.data["target_revision"], "status": "succeeded", "result_video_id": b_vid},
                format="json",
            )

            item_d.refresh_from_db()
            ord_c = staff_client.post(
                reverse("recording-ops-order-create", kwargs={"detail_id": detail_d.pk}),
                {"expected_revision": item_d.edl_revision},
                format="json",
            )
            self.assertEqual(ord_c.status_code, status.HTTP_201_CREATED)
            if outcome != "claim_proxy_expired":
                super_client.post(self.order_claim_url, {}, format="json")

            late_vid = f"lateA00000{idx}"
            if outcome == "claim_proxy_expired":
                RecordingReviewItem.objects.filter(pk=item_d.pk).update(
                    pending_stop_video_ids=[late_vid],
                    compensating_stop_pending=True,
                    proxy_expires_at=timezone.now() - timedelta(minutes=1),
                )
            else:
                late_r = super_client.post(
                    reverse("recording-ops-order-complete", kwargs={"order_id": old_d.data["id"]}),
                    {
                        "target_revision": old_d.data["target_revision"],
                        "status": "succeeded",
                        "result_video_id": late_vid,
                        "youtube_draft_privacy": "public",
                        "review_status": "published",
                    },
                    format="json",
                )
                self.assertEqual(late_r.status_code, status.HTTP_409_CONFLICT)

            if outcome == "unlisted_succeed":
                c_vid = f"nextC00000{idx}"
                res_c = super_client.post(
                    reverse("recording-ops-order-complete", kwargs={"order_id": ord_c.data["id"]}),
                    {
                        "target_revision": ord_c.data["target_revision"],
                        "status": "succeeded",
                        "result_video_id": c_vid,
                        "youtube_draft_privacy": "unlisted",
                    },
                    format="json",
                )
                self.assertEqual(res_c.status_code, status.HTTP_200_OK)
            elif outcome == "failed":
                res_c = super_client.post(
                    reverse("recording-ops-order-complete", kwargs={"order_id": ord_c.data["id"]}),
                    {
                        "target_revision": ord_c.data["target_revision"],
                        "status": "failed",
                        "error_message": "worker failed",
                    },
                    format="json",
                )
                self.assertEqual(res_c.status_code, status.HTTP_200_OK)
            elif outcome == "claim_sweep":
                RecordingEditOrder.objects.filter(pk=ord_c.data["id"]).update(
                    started_at=timezone.now() - timedelta(minutes=31)
                )
            elif outcome == "create_sweep":
                RecordingEditOrder.objects.filter(pk=ord_c.data["id"]).update(
                    started_at=timezone.now() - timedelta(minutes=31)
                )
                item_d.refresh_from_db()
                requeue_c = staff_client.post(
                    reverse("recording-ops-order-create", kwargs={"detail_id": detail_d.pk}),
                    {"expected_revision": item_d.edl_revision},
                    format="json",
                )
                self.assertEqual(requeue_c.status_code, status.HTTP_409_CONFLICT)
                self.assertEqual(requeue_c.data["code"], "active_order_exists")
            elif outcome == "claim_proxy_expired":
                pass
            else:
                RecordingEditOrder.objects.filter(pk=ord_c.data["id"]).update(
                    started_at=timezone.now() - timedelta(minutes=31)
                )
                late_c = super_client.post(
                    reverse("recording-ops-order-complete", kwargs={"order_id": ord_c.data["id"]}),
                    {"target_revision": ord_c.data["target_revision"], "status": "succeeded"},
                    format="json",
                )
                self.assertEqual(late_c.status_code, status.HTTP_409_CONFLICT)
                self.assertEqual(late_c.data["code"], "order_lease_expired")

            stopped_targets = []
            while len(stopped_targets) < 5:
                nxt = super_client.post(self.order_claim_url, {}, format="json")
                if nxt.data["order"] is None:
                    break
                self.assertEqual(nxt.data["order"]["order_type"], RecordingEditOrder.OrderType.STOP_PUBLISH)
                stopped_targets.append(nxt.data["order"]["target_video_id"])
                super_client.post(
                    reverse("recording-ops-order-complete", kwargs={"order_id": nxt.data["order"]["id"]}),
                    {
                        "target_revision": nxt.data["order"]["target_revision"],
                        "status": "succeeded",
                        "result_video_id": nxt.data["order"]["target_video_id"],
                    },
                    format="json",
                )
            self.assertIn(late_vid, stopped_targets)
            item_d.refresh_from_db()
            self.assertEqual(item_d.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)
            self.assertEqual(item_d.pending_stop_video_ids, [])
            self.assertFalse(item_d.compensating_stop_pending)

    def test_openapi_schema_includes_recording_ops_endpoints(self):
        response = self.client.get(f"{reverse('schema')}?format=json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = json.loads(response.content)
        paths = payload["paths"]

        machine_only = [
            ("/api/v1/recording-ops/items/{detail_id}/proxy-upload-url/", "post"),
            ("/api/v1/recording-ops/items/{detail_id}/", "put"),
            ("/api/v1/recording-ops/orders/claim/", "post"),
            ("/api/v1/recording-ops/orders/{order_id}/complete/", "post"),
        ]
        for path, method in machine_only:
            self.assertIn(path, paths)
            self.assertEqual(paths[path][method]["security"], [{"APIKeyAuth": []}])

        staff_paths = [
            ("/api/v1/recording-ops/items/{detail_id}/proxy-url/", "get"),
            ("/api/v1/recording-ops/items/{detail_id}/orders/", "post"),
            ("/api/v1/recording-ops/review-items/{review_item_id}/stop-publish/", "post"),
        ]
        for path, method in staff_paths:
            self.assertIn(path, paths)
            security = paths[path][method]["security"]
            self.assertEqual(security, [{"APIKeyAuth": []}])
            self.assertNotIn({}, security)
