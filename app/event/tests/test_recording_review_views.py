"""スタッフ運営用 録画プロキシ確認・開始終了トリミング確定 UI の単体・統合テスト。"""
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
from event.services.recording_ops.queries import (
    build_recording_detail_context,
    build_recording_list_queryset,
    format_seconds_display,
    get_recording_list_summary,
)
from event.services.recording_ops.storage import RecordingStorageError
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


def _valid_edl(*, start_sec: float = 30.0, end_sec: float = 900.0, max_sec: float = 1200.0) -> dict:
    return {
        "schema_version": 1,
        "safe_bounds": {"min_sec": 0.0, "max_sec": max_sec},
        "trim": {"start_sec": start_sec, "end_sec": end_sec},
        "boundary_candidates": {
            "start": [{"sec": 30.0, "label": "挨拶開始"}, 45.0],
            "end": [{"sec": 900.0, "label": "質疑終了"}, 930.0],
        },
        "cuts": [],
    }


def _proxy_key(detail_id: int) -> str:
    return f"recording-proxy/{detail_id}/{uuid.uuid4().hex}.mp4"


@_r2_settings()
class RecordingReviewStaffViewsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = make_discord_linked_user(user_name="rec_ui_staff", email="rec_ui_staff@example.com", is_staff=True)
        cls.superuser = make_discord_linked_user(
            user_name="rec_ui_super", email="rec_ui_super@example.com", is_staff=True, is_superuser=True,
        )
        cls.owner = make_discord_linked_user(user_name="rec_ui_owner", email="rec_ui_owner@example.com")
        cls.community_staff = make_discord_linked_user(user_name="rec_ui_cmember", email="rec_ui_cmember@example.com")
        cls.regular_user = make_discord_linked_user(user_name="rec_ui_user", email="rec_ui_user@example.com")

        cls.community = make_community(name="公開録画検証集会", owner=cls.owner, recording_allowed=True)
        make_community_member(cls.community, cls.community_staff, CommunityMember.Role.STAFF)
        cls.disallowed_community = make_community(name="非許可検証集会", owner=cls.owner, recording_allowed=False)

        cls.event = make_event(cls.community)
        cls.disallowed_event = make_event(cls.disallowed_community)

        cls.public_detail = make_event_detail(
            cls.event, status="approved", speaker="登壇者Public", theme="公開撮影できる発表",
            recording_policy=RecordingPolicy.PUBLIC,
        )
        cls.allowed_detail = make_event_detail(
            cls.event, status="approved", speaker="登壇者Allowed", theme="旧設定で非公開固定の発表",
            recording_policy=RecordingPolicy.ALLOWED,
        )
        cls.forbidden_detail = make_event_detail(
            cls.event, status="approved", speaker="登壇者Forbidden", theme="撮影禁止の発表",
            recording_policy=RecordingPolicy.FORBIDDEN,
        )
        cls.disallowed_detail = make_event_detail(
            cls.disallowed_event, status="approved", speaker="登壇者Disallowed", theme="集会許可OFFの発表",
            recording_policy=RecordingPolicy.PUBLIC,
        )

    def setUp(self):
        cache.clear()
        self.review_item = RecordingReviewItem.objects.create(
            event_detail=self.public_detail,
            proxy_r2_key=_proxy_key(self.public_detail.pk),
            proxy_duration_sec=1200.0,
            proxy_uploaded_at=timezone.now(),
            proxy_expires_at=timezone.now() + timedelta(days=7),
            review_status=RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
            edl_revision=1,
            edl_json=_valid_edl(),
        )

    def _list_url(self) -> str:
        return reverse("event:recording_review_list")

    def _detail_url(self, detail_id: int | None = None) -> str:
        return reverse("event:recording_review_detail", kwargs={"detail_id": detail_id or self.public_detail.pk})

    def test_staff_required_mixin_blocks_anonymous_and_non_staff_users(self):
        self.client.logout()
        for url in (self._list_url(), self._detail_url()):
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302)
            self.assertTrue(resp["Location"].startswith("/account/login/"))
            post_resp = self.client.post(url, {})
            self.assertEqual(post_resp.status_code, 302)

        for non_staff in (self.regular_user, self.owner, self.community_staff):
            self.client.force_login(non_staff)
            for url in (self._list_url(), self._detail_url()):
                with self.subTest(user=non_staff.user_name, url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)
                    self.assertEqual(self.client.post(url, {"expected_revision": 1}).status_code, 403)

    @patch("event.services.recording_ops.storage.boto3.client")
    def test_operator_navigation_links_appear_only_for_staff_and_superuser(self, mock_boto):
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://r2.example.invalid/signed?sig=abc"
        mock_boto.return_value = mock_s3

        for user in (self.owner, self.regular_user):
            self.client.force_login(user)
            settings_resp = self.client.get(reverse("account:settings"))
            self.assertNotContains(settings_resp, self._list_url())
            mylist_resp = self.client.get(reverse("event:my_list"))
            self.assertNotContains(mylist_resp, 'data-testid="recording-review-link"')

        for op in (self.staff, self.superuser):
            self.client.force_login(op)
            settings_resp = self.client.get(reverse("account:settings"))
            self.assertContains(settings_resp, self._list_url())
            self.assertContains(settings_resp, "録画・YouTube管理")
            mylist_resp = self.client.get(reverse("event:my_list"))
            self.assertContains(mylist_resp, 'data-testid="recording-review-link"')
            self.assertEqual(self.client.get(self._list_url()).status_code, 200)
            self.assertEqual(self.client.get(self._detail_url()).status_code, 200)

    @patch("event.services.recording_ops.storage.boto3.client")
    def test_consent_boundary_isolates_allowed_forbidden_and_disallowed_community(self, mock_boto):
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://r2.example.invalid/signed?sig=secret"
        mock_boto.return_value = mock_s3

        # allowed / forbidden にも仮の RecordingReviewItem が残っていても一切発給・導線表示しない
        RecordingReviewItem.objects.create(
            event_detail=self.allowed_detail,
            proxy_r2_key=_proxy_key(self.allowed_detail.pk),
            proxy_expires_at=timezone.now() + timedelta(days=7),
            review_status=RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
            edl_json=_valid_edl(),
        )
        RecordingReviewItem.objects.create(
            event_detail=self.forbidden_detail,
            proxy_r2_key=_proxy_key(self.forbidden_detail.pk),
            proxy_expires_at=timezone.now() + timedelta(days=7),
            review_status=RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
            edl_json=_valid_edl(),
        )

        self.client.force_login(self.staff)
        list_resp = self.client.get(self._list_url())
        self.assertEqual(list_resp.status_code, 200)
        soup = BeautifulSoup(list_resp.content, "html.parser")

        allowed_card = soup.select_one(f'[data-detail-id="{self.allowed_detail.pk}"]')
        self.assertIsNotNone(allowed_card)
        self.assertIn("非公開固定（旧設定）", allowed_card.get_text())
        self.assertIsNone(allowed_card.select_one('[data-testid="recording-detail-link"]'))
        self.assertNotIn("視聴可", allowed_card.get_text())

        forbidden_card = soup.select_one(f'[data-detail-id="{self.forbidden_detail.pk}"]')
        self.assertIsNotNone(forbidden_card)
        self.assertIn("非配信", forbidden_card.get_text())
        self.assertIsNone(forbidden_card.select_one('[data-testid="recording-detail-link"]'))

        mock_boto.reset_mock()
        for blocked_detail in (self.allowed_detail, self.forbidden_detail, self.disallowed_detail):
            url = self._detail_url(blocked_detail.pk)
            with self.subTest(detail=blocked_detail.pk):
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(
                    self.client.post(url, {"expected_revision": 1, "start_sec": 30, "end_sec": 900}).status_code,
                    403,
                )
                json_post = self.client.post(
                    url,
                    data=json.dumps({"expected_revision": 1, "start_sec": 30, "end_sec": 900}),
                    content_type="application/json",
                )
                self.assertEqual(json_post.status_code, 403)
        mock_boto.assert_not_called()

    @patch("event.services.recording_ops.storage.boto3.client")
    def test_detail_view_protects_presigned_url_headers_and_logs(self, mock_boto):
        secret_sig = "super-secret-r2-presigned-token-999"
        presigned = f"https://r2.example.invalid/vrc-ta-hub-private/video.mp4?X-Amz-Signature={secret_sig}"
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = presigned
        mock_boto.return_value = mock_s3

        self.client.force_login(self.staff)
        with self.assertLogs("event.services.recording_ops", level="INFO") as logs:
            resp = self.client.get(self._detail_url())

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["Referrer-Policy"], "same-origin")
        self.assertEqual(resp.headers["Cache-Control"], "private, no-store")

        joined_logs = "\n".join(logs.output)
        self.assertNotIn(secret_sig, joined_logs)
        self.assertNotIn("test-r2-secret", joined_logs)

        soup = BeautifulSoup(resp.content, "html.parser")
        video = soup.select_one("video#recording-proxy-player")
        self.assertIsNotNone(video)
        self.assertEqual(video["src"], presigned)
        self.assertEqual(video["referrerpolicy"], "no-referrer")
        # 署名 URL は video[src] 以外の場所（スクリプト設定や隠し入力等）に重複露出させない
        config_text = soup.select_one("#recording-review-config").get_text()
        self.assertNotIn(secret_sig, config_text)
        self.assertEqual(len(soup.find_all(string=lambda s: bool(s and secret_sig in s))), 0)

    def test_detail_view_handles_missing_expired_and_storage_failure_gracefully(self):
        self.client.force_login(self.staff)

        # 未登録（RecordingReviewItem 無し）
        no_item_detail = make_event_detail(
            self.event, status="approved", speaker="登壇者NoProxy", theme="未登録の発表",
            recording_policy=RecordingPolicy.PUBLIC,
        )
        resp_missing = self.client.get(self._detail_url(no_item_detail.pk))
        self.assertEqual(resp_missing.status_code, 200)
        self.assertContains(resp_missing, 'data-proxy-state="missing"')
        self.assertContains(resp_missing, "プロキシ動画が未登録です")
        soup = BeautifulSoup(resp_missing.content, "html.parser")
        self.assertTrue(soup.select_one("#recording-submit-btn").has_attr("disabled"))

        # 期限切れ
        self.review_item.proxy_expires_at = timezone.now() - timedelta(minutes=5)
        self.review_item.save(update_fields=["proxy_expires_at"])
        with self.assertLogs("event.services.recording_ops.queries", level="WARNING") as logs:
            resp_expired = self.client.get(self._detail_url())
        self.assertEqual(resp_expired.status_code, 200)
        self.assertContains(resp_expired, 'data-proxy-state="expired"')
        self.assertIn("proxy_expired", "\n".join(logs.output))

        # ストレージ発給エラー
        self.review_item.proxy_expires_at = timezone.now() + timedelta(days=7)
        self.review_item.save(update_fields=["proxy_expires_at"])
        with patch(
            "event.services.recording_ops.queries.create_presigned_proxy_download_url",
            side_effect=RecordingStorageError("boom"),
        ), self.assertLogs("event.services.recording_ops.queries", level="WARNING") as err_logs:
            resp_err = self.client.get(self._detail_url())
        self.assertEqual(resp_err.status_code, 200)
        self.assertContains(resp_err, 'data-proxy-state="error"')
        self.assertIn("storage_error", "\n".join(err_logs.output))

    @patch("event.services.recording_ops.storage.boto3.client")
    def test_trim_submit_creates_queued_order_and_handles_conflicts(self, mock_boto):
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://r2.example.invalid/signed?sig=ok"
        mock_boto.return_value = mock_s3

        self.client.force_login(self.staff)

        # JSON Fetch で expected_revision=1 確定成功
        with self.assertLogs("event.views.recording_review", level="INFO") as logs:
            ok_resp = self.client.post(
                self._detail_url(),
                data=json.dumps({"expected_revision": 1, "start_sec": 35.5, "end_sec": 880.0}),
                content_type="application/json",
            )
        self.assertEqual(ok_resp.status_code, 201)
        data = ok_resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["edl_revision"], 2)
        self.assertEqual(data["order_status"], RecordingEditOrder.Status.QUEUED)
        self.assertIn("recording_review_trim_order_submitted", "\n".join(logs.output))

        self.review_item.refresh_from_db()
        self.assertEqual(self.review_item.edl_revision, 2)
        self.assertEqual(self.review_item.edl_json["trim"], {"start_sec": 35.5, "end_sec": 880.0})
        self.assertEqual(self.review_item.edl_json["cuts"], [])
        order = RecordingEditOrder.objects.get(pk=data["order_id"])
        self.assertEqual(order.status, RecordingEditOrder.Status.QUEUED)
        self.assertEqual(order.requested_by, self.staff)

        # 詳細 GET では active_order 存在中により確定ボタン無効 + バナー表示
        detail_during_active = self.client.get(self._detail_url())
        soup = BeautifulSoup(detail_during_active.content, "html.parser")
        self.assertIsNotNone(soup.select_one('[data-testid="active-order-banner"]'))
        self.assertTrue(soup.select_one("#recording-submit-btn").has_attr("disabled"))

        # active_order 存在中の POST（JSON & Form）は 409 Conflict
        with self.assertLogs("event.views.recording_review", level="WARNING") as conflict_logs:
            active_conflict_json = self.client.post(
                self._detail_url(),
                data=json.dumps({"expected_revision": 2, "start_sec": 40.0, "end_sec": 800.0}),
                content_type="application/json",
            )
        self.assertEqual(active_conflict_json.status_code, 409)
        self.assertEqual(active_conflict_json.json()["code"], "active_order_exists")
        self.assertIn("recording_review_trim_conflict", "\n".join(conflict_logs.output))

        # オーダー完了後は expected_revision 不一致 (1 を送る) で 409 Conflict + 再読み込み導線
        order.status = RecordingEditOrder.Status.SUCCEEDED
        order.active_lock_key = None
        order.save(update_fields=["status", "active_lock_key"])

        stale_form_resp = self.client.post(
            self._detail_url(),
            {"expected_revision": "1", "start_sec": "40", "end_sec": "800"},
        )
        self.assertEqual(stale_form_resp.status_code, 409)
        stale_soup = BeautifulSoup(stale_form_resp.content, "html.parser")
        banner = stale_soup.select_one("#recording-conflict-banner")
        self.assertFalse(banner.has_attr("hidden"))
        self.assertIsNotNone(banner.select_one('[data-testid="reload-latest-btn"]'))
        self.assertTrue(stale_soup.select_one("#recording-submit-btn").has_attr("disabled"))
        self.assertTrue(stale_soup.select_one("#id_start_sec").has_attr("disabled"))
        self.assertEqual(stale_soup.select_one("#id_expected_revision").get("value"), "")

        # 最新リビジョン=2 で通常 Form POST すれば 302 PRG で成功
        form_ok_resp = self.client.post(
            self._detail_url(),
            {"expected_revision": "2", "start_sec": "42", "end_sec": "850"},
        )
        self.assertEqual(form_ok_resp.status_code, 302)
        self.review_item.refresh_from_db()
        self.assertEqual(self.review_item.edl_revision, 3)

    @patch("event.services.recording_ops.storage.boto3.client")
    def test_public_locked_item_disables_controls_and_rejects_submit(self, mock_boto):
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://r2.example.invalid/signed?sig=ok"
        mock_boto.return_value = mock_s3

        self.review_item.review_status = RecordingReviewItem.ReviewStatus.PUBLISHED
        self.review_item.youtube_draft_privacy = RecordingReviewItem.YouTubePrivacy.PUBLIC
        self.review_item.save(update_fields=["review_status", "youtube_draft_privacy"])
        self.assertTrue(self.review_item.is_public_locked)

        self.client.force_login(self.staff)
        resp = self.client.get(self._detail_url())
        self.assertEqual(resp.status_code, 200)
        soup = BeautifulSoup(resp.content, "html.parser")
        self.assertIsNotNone(soup.select_one('[data-testid="public-locked-banner"]'))
        self.assertTrue(soup.select_one("#id_start_sec").has_attr("disabled"))
        self.assertTrue(soup.select_one("#id_end_sec").has_attr("disabled"))
        self.assertTrue(soup.select_one("#recording-submit-btn").has_attr("disabled"))

        post_resp = self.client.post(
            self._detail_url(),
            data=json.dumps({"expected_revision": 1, "start_sec": 30.0, "end_sec": 900.0}),
            content_type="application/json",
        )
        self.assertEqual(post_resp.status_code, 409)
        self.assertEqual(post_resp.json()["code"], "public_locked")

    def test_validation_rejects_out_of_bounds_trim_and_non_finite(self):
        self.client.force_login(self.staff)
        for bad_payload in (
            {"expected_revision": 1, "start_sec": 0.0, "end_sec": 1500.0},
            {"expected_revision": 1, "start_sec": 500.0, "end_sec": 400.0},
            {"expected_revision": 1, "start_sec": True, "end_sec": 500.0},
            {"expected_revision": "1", "start_sec": 10.0, "end_sec": 500.0},
            {"expected_revision": 1, "start_sec": 10**400, "end_sec": 500.0},
        ):
            resp = self.client.post(
                self._detail_url(),
                data=json.dumps(bad_payload),
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 400, type(bad_payload.get("start_sec")))

        # 4301 桁整数の JSON ボディも 500 にせず 400 を返す
        huge_int_json = f'{{"expected_revision": {"1" * 4301}, "start_sec": 10.0, "end_sec": 500.0}}'
        resp_huge_json = self.client.post(self._detail_url(), data=huge_int_json, content_type="application/json")
        self.assertEqual(resp_huge_json.status_code, 400)

        # フォーム POST の Unicode 数字・超過整数・非有限数値も 400 を返す
        for bad_form in (
            {"expected_revision": "²", "start_sec": "10", "end_sec": "500"},
            {"expected_revision": "1" * 4301, "start_sec": "10", "end_sec": "500"},
            {"expected_revision": "", "start_sec": "10", "end_sec": "500"},
            {"expected_revision": "1", "start_sec": "1e400", "end_sec": "500"},
        ):
            resp_form = self.client.post(self._detail_url(), bad_form)
            self.assertEqual(resp_form.status_code, 400)

    def test_list_filters_summary_empty_state_and_terminology(self):
        self.client.force_login(self.staff)
        summary = get_recording_list_summary()
        self.assertEqual(summary.needs_review_count, 1)
        self.assertEqual(build_recording_list_queryset(status_filter="needs_review").count(), 1)
        self.assertEqual(build_recording_list_queryset(policy_filter="allowed").count(), 1)
        # 対象母集団: 撮影許可集会の 3 件（disallowed_detail は recording_allowed=False かつ未録画のため非対象）
        self.assertEqual(build_recording_list_queryset(order_filter="unconfirmed").count(), 3)
        self.assertEqual(build_recording_list_queryset(status_filter="unrecorded").count(), 0)
        self.assertEqual(build_recording_list_queryset(query="公開撮影できる").count(), 1)
        self.assertEqual(format_seconds_display(90.5), "1:30.5")
        self.assertEqual(format_seconds_display(3661.0), "1:01:01")

        # 詳細ページ 404（未存在 / 未承認）
        self.assertEqual(self.client.get(self._detail_url(9999999)).status_code, 404)
        pending_detail = make_event_detail(self.event, status="pending", theme="承認待ち発表", recording_policy=RecordingPolicy.PUBLIC)
        self.assertEqual(self.client.get(self._detail_url(pending_detail.pk)).status_code, 404)

        # 要対応バナーと空状態フィルタ
        list_resp = self.client.get(self._list_url())
        self.assertContains(list_resp, "要対応 1")
        self.assertContains(list_resp, "ボットによる自動撮影")
        self.assertNotContains(list_resp, "ライトニングトーク")

        empty_resp = self.client.get(self._list_url(), {"q": "絶対存在しない検索語999"})
        self.assertEqual(empty_resp.status_code, 200)
        self.assertContains(empty_resp, "条件に一致する発表はありません。")

        # 対象発表 0 件の空状態
        RecordingReviewItem.objects.all().delete()
        from event.models import EventDetail
        EventDetail.objects.all().delete()
        zero_resp = self.client.get(self._list_url())
        self.assertEqual(zero_resp.status_code, 200)
        self.assertContains(zero_resp, "録画管理の対象となる発表はまだありません。")
        self.assertIsNotNone(build_recording_detail_context)

    @patch("event.services.recording_ops.storage.boto3.client")
    def test_detail_ui_exposes_player_controls_and_hides_internal_terms(self, mock_boto: MagicMock):
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://r2.example.invalid/proxy.mp4?sig=1"
        mock_boto.return_value = mock_s3

        self.client.force_login(self.staff)
        resp = self.client.get(self._detail_url())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-testid="set-start-from-player-btn"')
        self.assertContains(resp, 'data-testid="set-end-from-player-btn"')
        self.assertContains(resp, 'data-testid="recording-mode-banner"')
        self.assertContains(resp, "event/css/recording-review.css")
        self.assertContains(resp, "event/js/recording-review-submit.js")
        # 画面上のラベル・案内文に内部識別子（keep_spans / cuts[] / trim.start_sec）が露出しないこと
        self.assertNotContains(resp, "keep_spans")
        self.assertNotContains(resp, "cuts[]")
        self.assertNotContains(resp, "trim.start_sec")
        self.assertNotContains(resp, "trim.end_sec")
