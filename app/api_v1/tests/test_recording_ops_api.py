"""動画編集プロキシ R2 署名配信・EDL v1 確定指示キュー API の結合テスト。"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.admin.sites import AdminSite
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from community.constants import RecordingPolicy
from event.admin import RecordingEditOrderAdmin, RecordingReviewItemAdmin
from event.models import EventDetail, RecordingEditOrder, RecordingReviewItem
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

        item = self._register_item()

        # 未認証は 401/403 + code
        anon = APIClient()
        for method, url in (
            ("post", self.upload_url),
            ("put", self.upsert_url),
            ("get", self.proxy_get_url),
            ("post", self.order_create_url),
            ("post", self.order_claim_url),
            ("post", reverse("recording-ops-order-complete", kwargs={"order_id": 1})),
        ):
            resp = getattr(anon, method)(url, {}, format="json")
            self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
            self.assertIn("code", resp.data)

        # 一般ユーザー・集会オーナー（is_staff=False）は全 6 エンドポイント 403
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

        # staff の read キーは GET proxy-url 可・POST orders 不可
        staff_read = self._api_client(self.staff_read_key)
        self.assertEqual(staff_read.get(self.proxy_get_url).status_code, status.HTTP_200_OK)
        read_order_resp = staff_read.post(self.order_create_url, {"expected_revision": 2}, format="json")
        self.assertEqual(read_order_resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(read_order_resp.data["code"], "read_only_api_key")

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
        item = self._register_item()

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

        item = self._register_item()

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

        # stop_publish を claim & complete しても公開済みロックは維持される
        stop_claim = super_client.post(self.order_claim_url, {}, format="json")
        self.assertEqual(stop_claim.data["order"]["id"], stop_resp.data["id"])
        stop_complete = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": stop_resp.data["id"]}),
            {"target_revision": 3, "status": "succeeded"},
            format="json",
        )
        self.assertEqual(stop_complete.status_code, status.HTTP_200_OK)
        item.refresh_from_db()
        self.assertEqual(item.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)
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

    def test_soft_deleted_detail_returns_404_and_consent_revoked_skips_youtube_url_update(self):
        staff_client = self._api_client(self.staff_write_key)
        super_client = self._api_client(self.super_write_key)

        self._register_item()
        order = staff_client.post(self.order_create_url, {"expected_revision": 1}, format="json")
        self.assertEqual(order.status_code, status.HTTP_201_CREATED)
        super_client.post(self.order_claim_url, {}, format="json")

        # 実行中に同意が forbidden に変更された場合、public 完了報告でも youtube_url への反映はスキップしつつロックは解放する
        EventDetail.objects.filter(pk=self.detail.pk).update(recording_policy=RecordingPolicy.FORBIDDEN)
        complete_resp = super_client.post(
            reverse("recording-ops-order-complete", kwargs={"order_id": order.data["id"]}),
            {
                "target_revision": 2,
                "status": "succeeded",
                "youtube_url": "https://youtu.be/abcdefghijk",
                "youtube_draft_privacy": "public",
            },
            format="json",
        )
        self.assertEqual(complete_resp.status_code, status.HTTP_200_OK)
        self.detail.refresh_from_db()
        self.assertIsNone(self.detail.youtube_url)

        # 論理削除後は通常操作と apply_edl は 404 だが、既存動画がある場合の stop_publish は作成できる
        self.detail.soft_delete()
        self.assertEqual(staff_client.get(self.proxy_get_url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            staff_client.post(self.order_create_url, {"expected_revision": 2}, format="json").status_code,
            status.HTTP_404_NOT_FOUND,
        )
        stop_after_delete = staff_client.post(
            self.order_create_url,
            {"expected_revision": 2, "order_type": "stop_publish"},
            format="json",
        )
        self.assertEqual(stop_after_delete.status_code, status.HTTP_201_CREATED)

    def test_unlisted_or_needs_review_completion_does_not_leak_youtube_url_and_bad_url_is_400(self):
        staff_client = self._api_client(self.staff_write_key)
        super_client = self._api_client(self.super_write_key)

        item = self._register_item()
        order = staff_client.post(self.order_create_url, {"expected_revision": 1}, format="json")
        self.assertEqual(order.status_code, status.HTTP_201_CREATED)
        super_client.post(self.order_claim_url, {}, format="json")
        complete_url = reverse("recording-ops-order-complete", kwargs={"order_id": order.data["id"]})

        # URL 解析エラーや矛盾した公開状態指定は 500 にせず 400
        self.assertEqual(
            super_client.post(
                complete_url,
                {"target_revision": 2, "status": "succeeded", "youtube_url": "https://["},
                format="json",
            ).status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(
            super_client.post(
                complete_url,
                {
                    "target_revision": 2,
                    "status": "succeeded",
                    "youtube_draft_privacy": "unlisted",
                    "review_status": "published",
                },
                format="json",
            ).status_code,
            status.HTTP_400_BAD_REQUEST,
        )

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

        # 巨大整数クエリも 400
        self.assertEqual(
            staff_client.get(f"{self.proxy_get_url}?expires_in={'9' * 5000}").status_code,
            status.HTTP_400_BAD_REQUEST,
        )

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
        ]
        for path, method in staff_paths:
            self.assertIn(path, paths)
            security = paths[path][method]["security"]
            self.assertEqual(security, [{"APIKeyAuth": []}])
            self.assertNotIn({}, security)
