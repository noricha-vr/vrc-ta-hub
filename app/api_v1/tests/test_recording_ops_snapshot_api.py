"""録画運用ダッシュボード向け統計スナップショット受信 API の結合テスト。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from django.contrib.admin.sites import AdminSite
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from api_v1.recording_ops_snapshot_serializers import (
    MAX_SNAPSHOT_PAYLOAD_BYTES,
    REQUIRED_SNAPSHOT_SECTIONS,
    RecordingOpsSnapshotIngestSerializer,
)
from event.admin import RecordingOpsSnapshotAdmin
from event.models import RecordingOpsSnapshot
from tests.factories import make_user
from user_account.models import APIKey

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "recording_ops_snapshot_v1.json"


def _load_contract_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


class RecordingOpsSnapshotAPITest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = make_user(
            user_name="snap_super",
            email="snap_super@example.com",
            is_staff=True,
            is_superuser=True,
        )
        cls.staff = make_user(
            user_name="snap_staff",
            email="snap_staff@example.com",
            is_staff=True,
            is_superuser=False,
        )
        cls.regular_user = make_user(
            user_name="snap_regular",
            email="snap_regular@example.com",
            is_staff=False,
            is_superuser=False,
        )

        _, cls.super_write_key = APIKey.create_with_raw_key(user=cls.superuser, name="snap-super-write")
        super_read_obj, cls.super_read_key = APIKey.create_with_raw_key(user=cls.superuser, name="snap-super-read")
        super_read_obj.scope = APIKey.SCOPE_READ
        super_read_obj.save(update_fields=["scope"])

        _, cls.staff_write_key = APIKey.create_with_raw_key(user=cls.staff, name="snap-staff-write")
        _, cls.regular_write_key = APIKey.create_with_raw_key(user=cls.regular_user, name="snap-regular-write")

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.url = reverse("recording-ops-snapshot")
        self.factory = RequestFactory()

    def _api_client(self, raw_key: str, *, header: str = "bearer") -> APIClient:
        client = APIClient()
        if header == "x-api-key":
            client.credentials(HTTP_X_API_KEY=raw_key)
        else:
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw_key}")
        return client

    def test_contract_fixture_is_valid_and_accepted(self):
        fixture = _load_contract_fixture()
        self.assertEqual(fixture["schema_version"], 1)
        for key in REQUIRED_SNAPSHOT_SECTIONS:
            self.assertIn(key, fixture)

        client = self._api_client(self.super_write_key)
        with self.assertLogs("api_v1.recording_ops_snapshot_views", level="INFO") as logs:
            response = client.post(self.url, fixture, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(RecordingOpsSnapshot.objects.count(), 1)
        snapshot = RecordingOpsSnapshot.objects.get()
        self.assertEqual(response.data["id"], snapshot.pk)
        self.assertEqual(response.data["schema_version"], 1)
        self.assertIn("snapshot_at", response.data)
        self.assertIn("received_at", response.data)
        self.assertEqual(snapshot.payload, fixture)
        self.assertTrue(any("recording_ops_snapshot_received" in line for line in logs.output))
        self.assertFalse(any(self.super_write_key in line for line in logs.output))

    def test_x_api_key_header_and_utc_z_timestamp_accepted(self):
        payload = _load_contract_fixture()
        payload["snapshot_at"] = "2026-11-24T12:00:00Z"
        payload["next_session"] = None

        response = self._api_client(self.super_write_key, header="x-api-key").post(
            self.url,
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        snapshot = RecordingOpsSnapshot.objects.get()
        self.assertIsNone(snapshot.payload["next_session"])

    def test_auth_scope_and_permission_boundaries(self):
        fixture = _load_contract_fixture()

        # 未認証は 401/403 + code
        anon_resp = APIClient().post(self.url, fixture, format="json")
        self.assertIn(anon_resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
        self.assertIn("code", anon_resp.data)

        # セッション認証のみ（APIKey なし）は superuser でも 403
        session_client = APIClient()
        session_client.force_login(self.superuser)
        session_resp = session_client.post(self.url, fixture, format="json")
        self.assertEqual(session_resp.status_code, status.HTTP_403_FORBIDDEN)

        # read-only API キーは 403 + read_only_api_key
        read_resp = self._api_client(self.super_read_key).post(self.url, fixture, format="json")
        self.assertEqual(read_resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(read_resp.data["code"], "read_only_api_key")

        # スタッフ・一般ユーザーの write API キーは 403（IsSuperuser 必須）
        for raw_key in (self.staff_write_key, self.regular_write_key):
            resp = self._api_client(raw_key).post(self.url, fixture, format="json")
            self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        self.assertEqual(RecordingOpsSnapshot.objects.count(), 0)

    def test_rejects_invalid_schema_version_and_snapshot_at(self):
        client = self._api_client(self.super_write_key)
        base = _load_contract_fixture()

        for bad_version in (0, 2, "1", True, 1.0, None):
            payload = copy.deepcopy(base)
            payload["schema_version"] = bad_version
            resp = client.post(self.url, payload, format="json")
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, f"version={bad_version!r}")

        for bad_ts in ("2026-11-24T21:00:00", "2026-11-24", "not-a-date", 1732449600, None):
            payload = copy.deepcopy(base)
            payload["snapshot_at"] = bad_ts
            resp = client.post(self.url, payload, format="json")
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, f"snapshot_at={bad_ts!r}")

        self.assertEqual(RecordingOpsSnapshot.objects.count(), 0)

    def test_section_type_checks_and_unknown_keys_compatibility(self):
        client = self._api_client(self.super_write_key)
        base = _load_contract_fixture()

        # 必須セクション欠落は 400
        for missing_key in REQUIRED_SNAPSHOT_SECTIONS:
            payload = copy.deepcopy(base)
            del payload[missing_key]
            resp = client.post(self.url, payload, format="json")
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, f"missing={missing_key}")

        # 5 セクションの型違反は 400
        invalid_sections = {
            "next_session": [[], "str", 123, True],
            "action_items": [{}, "str", 123, None, False],
            "funnel_stats": [[], "str", 123, None, True],
            "venue_coverage": [[], "str", 123, None, False],
            "upcoming_schedule": [{}, "str", 123, None, True],
        }
        for section, bad_values in invalid_sections.items():
            for bad_value in bad_values:
                payload = copy.deepcopy(base)
                payload[section] = bad_value
                resp = client.post(self.url, payload, format="json")
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, f"{section}={bad_value!r}")

        # ルート非 dict も 400
        list_resp = client.post(self.url, [base], format="json")
        self.assertEqual(list_resp.status_code, status.HTTP_400_BAD_REQUEST)

        # トップレベルおよび各セクション内の未知キーは許容して保存する
        compat_payload = copy.deepcopy(base)
        compat_payload["future_top_level_field"] = {"enabled": True}
        compat_payload["next_session"]["future_flag"] = "ok"
        compat_payload["action_items"][0]["priority"] = "high"
        compat_payload["funnel_stats"]["extra_note"] = "v1.1"
        compat_payload["venue_coverage"]["custom_count"] = 3
        compat_payload["upcoming_schedule"][0]["extra_meta"] = {"kind": "note"}

        ok_resp = client.post(self.url, compat_payload, format="json")
        self.assertEqual(ok_resp.status_code, status.HTTP_201_CREATED, ok_resp.data)
        saved = RecordingOpsSnapshot.objects.get()
        self.assertEqual(saved.payload["future_top_level_field"], {"enabled": True})
        self.assertEqual(saved.payload["next_session"]["future_flag"], "ok")
        self.assertEqual(saved.payload["upcoming_schedule"][0]["extra_meta"], {"kind": "note"})

    def test_rejects_payload_exceeding_max_bytes(self):
        client = self._api_client(self.super_write_key)
        base = _load_contract_fixture()
        base["padding"] = ""

        baseline = len(json.dumps(base, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        max_pad = MAX_SNAPSHOT_PAYLOAD_BYTES - baseline
        self.assertGreater(max_pad, 0)

        # 上限ちょうどは 201
        exact_payload = copy.deepcopy(base)
        exact_payload["padding"] = "a" * max_pad
        exact_raw = json.dumps(exact_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertEqual(len(exact_raw), MAX_SNAPSHOT_PAYLOAD_BYTES)
        exact_resp = client.post(self.url, exact_raw, content_type="application/json")
        self.assertEqual(exact_resp.status_code, status.HTTP_201_CREATED, exact_resp.data)

        # 1 バイト超過は 400
        over_payload = copy.deepcopy(base)
        over_payload["padding"] = "a" * (max_pad + 1)
        over_raw = json.dumps(over_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertEqual(len(over_raw), MAX_SNAPSHOT_PAYLOAD_BYTES + 1)
        with self.assertLogs("api_v1.recording_ops_snapshot_views", level="WARNING") as logs:
            over_resp = client.post(self.url, over_raw, content_type="application/json")
        self.assertEqual(over_resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(any("recording_ops_snapshot_rejected" in line for line in logs.output))

        # Serializer 単体呼び出しでも上限超過を拒否する
        serializer = RecordingOpsSnapshotIngestSerializer(data=over_payload)
        self.assertFalse(serializer.is_valid())
        self.assertEqual(RecordingOpsSnapshot.objects.count(), 1)

    def test_rejects_forbidden_vrchat_patterns_anywhere_in_payload(self):
        client = self._api_client(self.super_write_key)
        base = _load_contract_fixture()

        forbidden_samples = [
            ("next_session_value", lambda p: p["next_session"].__setitem__("notes", "wrld_00000000-0000-0000-0000-000000000000")),
            ("action_item_value", lambda p: p["action_items"][0].__setitem__("reason", "usr_12345678-1234-1234-1234-123456789012")),
            ("upcoming_nested", lambda p: p["upcoming_schedule"][0]["talks"][0].__setitem__("theme", "grp_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")),
            ("venue_inst", lambda p: p["venue_coverage"].__setitem__("debug", "inst_12345")),
            ("unknown_key_uri", lambda p: p.__setitem__("extra", "VRCHAT://launch?id=123")),
            ("dict_key_name", lambda p: p["funnel_stats"].__setitem__("wrld_key", 1)),
        ]
        for label, mutator in forbidden_samples:
            payload = copy.deepcopy(base)
            mutator(payload)
            resp = client.post(self.url, payload, format="json")
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, label)

        # NaN も 400 で拒否する
        nan_raw = json.dumps(base, ensure_ascii=False).replace('"talk_count": 2', '"talk_count": NaN', 1).encode("utf-8")
        nan_resp = client.post(self.url, nan_raw, content_type="application/json")
        self.assertEqual(nan_resp.status_code, status.HTTP_400_BAD_REQUEST)

        self.assertEqual(RecordingOpsSnapshot.objects.count(), 0)

    def test_admin_is_read_only_and_openapi_schema_includes_endpoint(self):
        admin_site = AdminSite()
        model_admin = RecordingOpsSnapshotAdmin(RecordingOpsSnapshot, admin_site)
        req = self.factory.get("/admin/")
        req.user = self.superuser
        self.assertFalse(model_admin.has_add_permission(req))
        self.assertFalse(model_admin.has_change_permission(req))
        self.assertFalse(model_admin.has_delete_permission(req))

        schema_resp = APIClient().get(f"{reverse('schema')}?format=json")
        self.assertEqual(schema_resp.status_code, status.HTTP_200_OK)
        schema = json.loads(schema_resp.content)
        self.assertIn("/api/v1/recording-ops/snapshot/", schema["paths"])
        post_op = schema["paths"]["/api/v1/recording-ops/snapshot/"]["post"]
        self.assertEqual(post_op["security"], [{"APIKeyAuth": []}])
