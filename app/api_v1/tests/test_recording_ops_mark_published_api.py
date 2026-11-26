"""録画運用向け YouTube 公開完了同期 API（mark-published）のテスト。"""
from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from event.models import EventDetail, RecordingEditOrder, RecordingReviewItem
from tests.factories import make_community, make_event, make_event_detail, make_user
from user_account.models import APIKey


class RecordingOpsMarkPublishedAPITest(TestCase):
    def setUp(self):
        self.superuser = make_user(
            user_name="mp_super", email="mp_super@example.com", is_staff=True, is_superuser=True
        )
        self.staff = make_user(
            user_name="mp_staff", email="mp_staff@example.com", is_staff=True, is_superuser=False
        )
        self.normal = make_user(
            user_name="mp_normal", email="mp_normal@example.com", is_staff=False, is_superuser=False
        )
        _, self.super_write_key = APIKey.create_with_raw_key(user=self.superuser, name="mp-super-write")
        read_obj, self.super_read_key = APIKey.create_with_raw_key(user=self.superuser, name="mp-super-read")
        read_obj.scope = APIKey.SCOPE_READ
        read_obj.save(update_fields=["scope"])
        _, self.staff_key = APIKey.create_with_raw_key(user=self.staff, name="mp-staff")
        _, self.normal_key = APIKey.create_with_raw_key(user=self.normal, name="mp-normal")

        self.community = make_community(name="公開同期集会", status="approved", recording_allowed=True)
        self.event = make_event(self.community, event_date=date(2026, 11, 26))

    def _client(self, raw_key: str | None = None) -> APIClient:
        client = APIClient()
        if raw_key:
            client.credentials(HTTP_X_API_KEY=raw_key)
        return client

    def _make_detail(self, **kwargs) -> EventDetail:
        defaults = dict(
            detail_type="LT",
            status="approved",
            speaker="登壇者",
            theme="テーマ",
            recording_policy=EventDetail.RecordingPolicy.PUBLIC,
        )
        defaults.update(kwargs)
        return make_event_detail(self.event, **defaults)

    def _make_item(self, detail: EventDetail, **kwargs) -> RecordingReviewItem:
        defaults = dict(
            event_detail=detail,
            proxy_r2_key=f"recording-proxy/{detail.pk}/sample.mp4",
            review_status=RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED,
            youtube_draft_video_id="dQw4w9WgXcQ",
            youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.UNLISTED,
        )
        defaults.update(kwargs)
        return RecordingReviewItem.objects.create(**defaults)

    def _url(self, detail_id: int) -> str:
        return reverse("recording-ops-mark-published", kwargs={"detail_id": detail_id})

    def test_permissions_api_key_superuser_write_only(self):
        detail = self._make_detail()
        self._make_item(detail)
        url = self._url(detail.pk)

        # 未認証・一般・スタッフ・read スコープは拒否
        self.assertIn(self._client().post(url, {"video_id": "dQw4w9WgXcQ"}, format="json").status_code, (401, 403))
        for key in (self.normal_key, self.staff_key, self.super_read_key):
            resp = self._client(key).post(url, {"video_id": "dQw4w9WgXcQ"}, format="json")
            self.assertEqual(resp.status_code, 403)

        # セッション認証のみも拒否（API キー限定）
        session_client = APIClient()
        session_client.force_login(self.superuser)
        resp = session_client.post(url, {"video_id": "dQw4w9WgXcQ"}, format="json")
        self.assertIn(resp.status_code, (401, 403))

    def test_no_review_item_returns_200_idempotent_skip(self):
        detail = self._make_detail()
        resp = self._client(self.super_write_key).post(
            self._url(detail.pk), {"video_id": "dQw4w9WgXcQ"}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["synced"])
        self.assertEqual(resp.json()["reason"], "no_review_item")

    def test_sync_transitions_to_published_and_is_idempotent(self):
        detail = self._make_detail()
        item = self._make_item(detail, review_status=RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
                               youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.PRIVATE)
        resp = self._client(self.super_write_key).post(
            self._url(detail.pk), {"video_id": "dQw4w9WgXcQ"}, format="json"
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(resp.json()["synced"])
        self.assertFalse(resp.json()["already_published"])
        self.assertTrue(resp.json()["is_public_locked"])

        item.refresh_from_db()
        self.assertEqual(item.review_status, RecordingReviewItem.ReviewStatus.PUBLISHED)
        self.assertEqual(item.youtube_draft_privacy, RecordingReviewItem.YouTubePrivacy.PUBLIC)
        self.assertIsNotNone(item.public_locked_at)
        self.assertEqual(item.edl_revision, 1)

        # 2 回目は冪等成功
        resp2 = self._client(self.super_write_key).post(
            self._url(detail.pk), {"video_id": "dQw4w9WgXcQ"}, format="json"
        )
        self.assertEqual(resp2.status_code, 200)
        self.assertTrue(resp2.json()["already_published"])

    def test_allowlist_rejects_disallowed_states_with_409(self):
        # 1. private_fixed / processing / expired / unrecorded
        for bad_status in (
            RecordingReviewItem.ReviewStatus.PRIVATE_FIXED,
            RecordingReviewItem.ReviewStatus.PROCESSING,
            RecordingReviewItem.ReviewStatus.EXPIRED,
            RecordingReviewItem.ReviewStatus.UNRECORDED,
        ):
            detail = self._make_detail()
            self._make_item(detail, review_status=bad_status)
            resp = self._client(self.super_write_key).post(
                self._url(detail.pk), {"video_id": "dQw4w9WgXcQ"}, format="json"
            )
            self.assertEqual(resp.status_code, 409, msg=bad_status)
            self.assertEqual(resp.json()["code"], "invalid_review_status")

        # 2. video_id 不一致・空
        detail2 = self._make_detail()
        self._make_item(detail2, youtube_draft_video_id="other123456")
        resp2 = self._client(self.super_write_key).post(
            self._url(detail2.pk), {"video_id": "dQw4w9WgXcQ"}, format="json"
        )
        self.assertEqual(resp2.status_code, 409)
        self.assertEqual(resp2.json()["code"], "video_id_mismatch")

        # 3. 進行中オーダーあり
        detail3 = self._make_detail()
        item3 = self._make_item(detail3)
        RecordingEditOrder.objects.create(
            review_item=item3,
            event_detail=detail3,
            order_type=RecordingEditOrder.OrderType.APPLY_EDL,
            base_revision=1,
            target_revision=2,
            status=RecordingEditOrder.Status.QUEUED,
            active_lock_key=f"item:{item3.pk}",
        )
        resp3 = self._client(self.super_write_key).post(
            self._url(detail3.pk), {"video_id": "dQw4w9WgXcQ"}, format="json"
        )
        self.assertEqual(resp3.status_code, 409)
        self.assertEqual(resp3.json()["code"], "active_order_in_progress")

    def test_consent_revoked_or_deleted_enqueues_compensating_stop_and_clears_url(self):
        # 同意撤回後に YouTube 公開が走ってしまったレース
        detail = self._make_detail(
            recording_policy=EventDetail.RecordingPolicy.FORBIDDEN,
            youtube_url="https://youtu.be/dQw4w9WgXcQ",
        )
        item = self._make_item(
            detail,
            review_status=RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
            youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.PRIVATE,
        )
        resp = self._client(self.super_write_key).post(
            self._url(detail.pk), {"video_id": "dQw4w9WgXcQ"}, format="json"
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["code"], "consent_revoked_stop_enqueued")

        detail.refresh_from_db()
        item.refresh_from_db()
        self.assertIsNone(detail.youtube_url)
        self.assertIn("dQw4w9WgXcQ", item.pending_stop_video_ids)
        self.assertTrue(
            RecordingEditOrder.objects.filter(
                review_item=item,
                order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
                status=RecordingEditOrder.Status.QUEUED,
                target_video_id="dQw4w9WgXcQ",
            ).exists()
        )

        # 論理削除済みでも同様に補償停止を積む
        detail_del = self._make_detail(deleted_at=timezone.now(), youtube_url="https://youtu.be/abc12345678")
        item_del = self._make_item(detail_del, youtube_draft_video_id="abc12345678")
        resp_del = self._client(self.super_write_key).post(
            self._url(detail_del.pk), {"video_id": "abc12345678"}, format="json"
        )
        self.assertEqual(resp_del.status_code, 409)
        self.assertEqual(resp_del.json()["code"], "consent_revoked_stop_enqueued")
        self.assertTrue(
            RecordingEditOrder.objects.filter(
                review_item=item_del,
                order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
                status=RecordingEditOrder.Status.QUEUED,
            ).exists()
        )

    def test_running_stop_publish_race_enqueues_followup_compensating_stop(self):
        from event.services.recording_ops.completion import complete_edit_order

        detail = self._make_detail(recording_policy=EventDetail.RecordingPolicy.FORBIDDEN)
        item = self._make_item(detail, youtube_draft_video_id="race1234567")
        running_stop = RecordingEditOrder.objects.create(
            review_item=item,
            event_detail=detail,
            order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
            target_video_id="race1234567",
            base_revision=1,
            target_revision=2,
            status=RecordingEditOrder.Status.RUNNING,
            active_lock_key=f"item:{item.pk}",
            started_at=timezone.now(),
        )
        # 実行中 stop_publish の間に同動画が再公開されて mark-published が到達
        resp = self._client(self.super_write_key).post(
            self._url(detail.pk), {"video_id": "race1234567"}, format="json"
        )
        self.assertEqual(resp.status_code, 409)
        item.refresh_from_db()
        self.assertTrue(item.compensating_stop_pending)

        # 古い stop_publish が完了しても private_fixed に落ちず、追従の補償 stop_publish が積まれること
        complete_edit_order(
            running_stop.pk,
            target_revision=2,
            status=RecordingEditOrder.Status.SUCCEEDED,
            result_video_id="race1234567",
            youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.PRIVATE,
            review_status=RecordingReviewItem.ReviewStatus.PRIVATE_FIXED,
        )
        item.refresh_from_db()
        self.assertNotEqual(item.review_status, RecordingReviewItem.ReviewStatus.PRIVATE_FIXED)
        self.assertTrue(
            RecordingEditOrder.objects.filter(
                review_item=item,
                order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
                status=RecordingEditOrder.Status.QUEUED,
                target_video_id="race1234567",
            ).exists()
        )

    def test_openapi_schema_includes_mark_published(self):
        resp = APIClient().get(reverse("schema"), HTTP_ACCEPT="application/vnd.oai.openapi+json")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("/api/v1/recording-ops/items/{detail_id}/mark-published/", resp.json()["paths"])
