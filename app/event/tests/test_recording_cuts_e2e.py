"""運営スタッフ用 録画途中カット cuts[] UI の実ブラウザ E2E テスト。"""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest import skipUnless
from unittest.mock import MagicMock, patch

from django.test import override_settings, tag
from django.urls import reverse
from django.utils import timezone

from community.constants import RecordingPolicy
from event.models import RecordingEditOrder, RecordingReviewItem
from event.tests.test_recording_review_e2e import _build_sample_mp4
from tests.factories import (
    make_community,
    make_discord_linked_user,
    make_event,
    make_event_detail,
)
from website.tests.e2e.test_user_journeys import PLAYWRIGHT_AVAILABLE, PlaywrightLiveServerTestCase

try:
    from playwright.sync_api import expect
except ModuleNotFoundError:
    expect = None


def _edl_with_cuts() -> dict:
    return {
        "schema_version": 1,
        "safe_bounds": {"min_sec": 0.0, "max_sec": 15.0},
        "trim": {"start_sec": 1.0, "end_sec": 13.0},
        "boundary_candidates": {
            "start": [{"sec": 1.0, "label": "挨拶開始"}],
            "end": [{"sec": 13.0, "label": "質疑終了"}],
        },
        "cuts": [
            {
                "id": "auto-1",
                "start_sec": 4.0,
                "end_sec": 6.0,
                "kind": "silence",
                "label": "無音区間A",
                "source": "vad",
                "confidence": 0.94,
                "action": "pending",
            },
            {
                "id": "auto-2",
                "start_sec": 10.5,
                "end_sec": 12.0,
                "kind": "trouble",
                "label": "機材確認B",
                "source": "vad",
                "confidence": 0.81,
                "action": "pending",
            },
        ],
    }


@tag("e2e", "browser")
@skipUnless(PLAYWRIGHT_AVAILABLE, "Playwright E2E dependencies are not installed")
@override_settings(
    DEBUG_LOGIN_SKIP=False,
    DISCORD_AUTH_REQUIRED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    AWS_ACCESS_KEY_ID="test-r2-key",
    AWS_SECRET_ACCESS_KEY="test-r2-secret",
    AWS_STORAGE_BUCKET_NAME="vrc-ta-hub-public",
    AWS_PRIVATE_STORAGE_BUCKET_NAME="vrc-ta-hub-private",
    AWS_S3_ENDPOINT_URL="https://r2.example.invalid",
)
class RecordingCutsE2ETests(PlaywrightLiveServerTestCase):
    """途中カット cuts[] の採否・手動追加/編集/削除・フラグ抑止・PC/スマホ表示を検証する。"""

    password = "e2e-cuts-pass"
    mp4_bytes: bytes = b""
    tmp_dir: tempfile.TemporaryDirectory[str] | None = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp_dir = tempfile.TemporaryDirectory(prefix="vrc-ta-hub-cuts-e2e-")
        cls.mp4_bytes = _build_sample_mp4(Path(cls.tmp_dir.name) / "sample_proxy.mp4")
        super().setUpClass()

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            super().tearDownClass()
        finally:
            if cls.tmp_dir is not None:
                cls.tmp_dir.cleanup()
                cls.tmp_dir = None

    def setUp(self) -> None:
        super().setUp()
        self.shot_dir = Path(
            os.environ.get("RECORDING_CUTS_SCREENSHOT_DIR")
            or (Path(__file__).resolve().parents[3] / "test-results" / "e2e" / "recording_cuts")
        )
        self.shot_dir.mkdir(parents=True, exist_ok=True)
        self.page_errors: list[str] = []
        self.page.on("pageerror", lambda exc: self.page_errors.append(str(exc)))
        self.staff = make_discord_linked_user(
            user_name="e2e_cuts_staff", email="e2e-cuts-staff@example.test", password=self.password, is_staff=True,
        )
        owner = make_discord_linked_user(
            user_name="e2e_cuts_owner", email="e2e-cuts-owner@example.test", password=self.password,
        )
        self.community = make_community(name="E2Eカット検証集会", owner=owner, recording_allowed=True)
        self.event = make_event(self.community)
        self.detail = make_event_detail(
            self.event,
            status="approved",
            speaker="登壇者Cuts",
            theme="途中カット UI と自動候補採否の統合検証",
            recording_policy=RecordingPolicy.PUBLIC,
        )
        self.review_item = RecordingReviewItem.objects.create(
            event_detail=self.detail,
            proxy_r2_key=f"recording-proxy/{self.detail.pk}/{uuid.uuid4().hex}.mp4",
            proxy_duration_sec=15.0,
            proxy_uploaded_at=timezone.now(),
            proxy_expires_at=timezone.now() + timedelta(days=7),
            review_status=RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
            edl_revision=1,
            edl_json=_edl_with_cuts(),
        )
        self.page.route("https://r2.example.invalid/**", self._fulfill_mock_mp4)

    def _fulfill_mock_mp4(self, route: Any) -> None:
        total = len(self.mp4_bytes)
        range_header = route.request.headers.get("range", "")
        if range_header.startswith("bytes="):
            spec = range_header.replace("bytes=", "", 1).split(",")[0].strip()
            start_str, _, end_str = spec.partition("-")
            start = int(start_str) if start_str else 0
            end = min(int(end_str) if end_str else total - 1, total - 1)
            chunk = self.mp4_bytes[start : end + 1]
            route.fulfill(
                status=206,
                body=chunk,
                headers={
                    "Content-Type": "video/mp4",
                    "Accept-Ranges": "bytes",
                    "Content-Range": f"bytes {start}-{end}/{total}",
                    "Content-Length": str(len(chunk)),
                },
            )
            return
        route.fulfill(
            status=200,
            body=self.mp4_bytes,
            headers={
                "Content-Type": "video/mp4",
                "Accept-Ranges": "bytes",
                "Content-Length": str(total),
            },
        )

    def _assert_no_horizontal_overflow(self, label: str) -> None:
        overflow = self.page.evaluate(
            "() => ({scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth})"
        )
        self.assertLessEqual(
            overflow["scrollWidth"],
            overflow["clientWidth"],
            f"Horizontal overflow detected in {label}: {overflow}",
        )

    @patch("event.services.recording_ops.storage.boto3.client")
    def test_default_flag_off_guards_accept_and_confirms_pending(self, mock_boto: MagicMock) -> None:
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://r2.example.invalid/vrc-ta-hub-private/sample.mp4?X-Amz-Signature=sig1"
        mock_boto.return_value = mock_s3

        self.login(self.staff.email, self.password)
        detail_url = f"{self.live_server_url}{reverse('event:recording_review_detail', kwargs={'detail_id': self.detail.pk})}"
        self.page.set_viewport_size({"width": 1280, "height": 900})
        self.page.goto(detail_url)
        self.page.wait_for_function(
            "() => { const v = document.getElementById('recording-proxy-player'); return v && v.readyState >= 1; }"
        )

        expect(self.page.get_by_test_id("accept-cuts-disabled-notice")).to_be_visible()
        expect(self.page.locator('[data-admin-form-status="recording-trim-form"]')).to_contain_text("変更はありません")
        expect(self.page.get_by_test_id("cut-row")).to_have_count(2)

        # 採用へ切り替えると抑止バナーが出て確定ボタンが無効化される
        first_row = self.page.get_by_test_id("cut-row").nth(0)
        first_row.get_by_test_id("cut-action-accept").click()
        expect(self.page.get_by_test_id("cuts-accept-blocked-banner")).to_be_visible()
        expect(self.page.get_by_test_id("recording-submit-btn")).to_be_disabled()
        expect(self.page.locator('[data-admin-form-status="recording-trim-form"]')).to_contain_text("未保存の変更があります")
        self._assert_no_horizontal_overflow("cuts_disabled_pc")
        self.page.screenshot(path=str(self.shot_dir / "cuts_disabled_pc.png"), full_page=True)

        # 却下へ戻すと確定可能になり、残り pending は確認チェック必須
        first_row.get_by_test_id("cut-action-reject").click()
        expect(self.page.get_by_test_id("cuts-accept-blocked-banner")).to_be_hidden()
        expect(self.page.get_by_test_id("recording-submit-btn")).to_be_enabled()
        expect(self.page.get_by_test_id("pending-cuts-warning")).to_be_visible()

        self.page.get_by_test_id("recording-submit-btn").click()
        expect(self.page.locator("#recording-submit-error")).to_be_visible()

        self.page.get_by_test_id("confirm-pending-cuts-checkbox").check()
        self.page.get_by_test_id("recording-submit-btn").click()
        expect(self.page.locator("#recording-submit-success")).to_be_visible()

        order = RecordingEditOrder.objects.get(review_item=self.review_item)
        self.assertEqual([c["action"] for c in order.edl_snapshot["cuts"]], ["reject", "pending"])
        self.assertEqual(self.page_errors, [])

    @patch("event.services.recording_ops.edl.ALLOW_ACCEPT_CUTS", True)
    @patch("event.services.recording_ops.storage.boto3.client")
    def test_accept_manual_edit_delete_timeline_and_mobile_screenshots(self, mock_boto: MagicMock) -> None:
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://r2.example.invalid/vrc-ta-hub-private/sample.mp4?X-Amz-Signature=sig2"
        mock_boto.return_value = mock_s3

        self.login(self.staff.email, self.password)
        detail_url = f"{self.live_server_url}{reverse('event:recording_review_detail', kwargs={'detail_id': self.detail.pk})}"
        self.page.set_viewport_size({"width": 1280, "height": 900})
        self.page.goto(detail_url)
        self.page.wait_for_function(
            "() => { const v = document.getElementById('recording-proxy-player'); return v && v.readyState >= 1; }"
        )
        expect(self.page.get_by_test_id("accept-cuts-disabled-notice")).to_have_count(0)

        # 前後を再生で対象候補付近へシーク
        first_row = self.page.get_by_test_id("cut-row").nth(0)
        first_row.get_by_test_id("cut-preview-btn").click()
        self.page.wait_for_function(
            "() => { const v = document.getElementById('recording-proxy-player'); return v && v.currentTime >= 1.3 && v.currentTime <= 5.0; }"
        )
        self.page.evaluate("() => document.getElementById('recording-proxy-player').pause()")

        # 候補 1 を採用（trim 1..13 = 12s から 4..6 = 2s カット → 10.0s）
        first_row.get_by_test_id("cut-action-accept").click()
        expect(self.page.locator("#recording-adopted-duration")).to_contain_text("10.0秒")
        expect(self.page.get_by_test_id("keep-spans-summary")).to_contain_text("2区間")

        # 手動カット追加（7.5 〜 9.5 秒）→ 採用尺 8.0s
        self.page.locator("#cut-add-start").fill("7.5")
        self.page.locator("#cut-add-end").fill("9.5")
        self.page.locator("#cut-add-kind").select_option("break")
        self.page.locator("#cut-add-label").fill("マイク再接続")
        self.page.get_by_test_id("add-manual-cut-btn").click()
        expect(self.page.get_by_test_id("cut-row")).to_have_count(3)
        expect(self.page.locator("#recording-adopted-duration")).to_contain_text("8.0秒")

        # 手動カット（中央の行 index 1）をインライン編集（end 9.5 → 9.0 で採用尺 8.5s）
        manual_row = self.page.get_by_test_id("cut-row").nth(1)
        manual_row.get_by_test_id("cut-edit-btn").click()
        manual_row.get_by_test_id("cut-edit-end").fill("9")
        manual_row.get_by_test_id("cut-edit-label").fill("マイク再接続済み")
        manual_row.get_by_test_id("cut-edit-apply").click()
        expect(manual_row).to_contain_text("マイク再接続済み")
        expect(self.page.locator("#recording-adopted-duration")).to_contain_text("8.5秒")

        # 候補 2（index 2）を削除 → pending 0 件になり警告枠が消える
        self.page.get_by_test_id("cut-row").nth(2).get_by_test_id("cut-delete-btn").click()
        expect(self.page.get_by_test_id("cut-row")).to_have_count(2)
        expect(self.page.get_by_test_id("pending-cuts-warning")).to_be_hidden()

        # プレビュー再生中は accept カット (4..6) を自動でスキップする
        self.page.evaluate(
            "() => { const v = document.getElementById('recording-proxy-player'); v.currentTime = 4.2; v.play(); }"
        )
        self.page.wait_for_function(
            "() => { const v = document.getElementById('recording-proxy-player'); return v && v.currentTime >= 5.9; }"
        )
        self.page.evaluate("() => document.getElementById('recording-proxy-player').pause()")

        # PC 1280px & スマホ 390px スクショと横溢れなし確認
        self._assert_no_horizontal_overflow("cuts_active_pc")
        self.page.screenshot(path=str(self.shot_dir / "cuts_active_pc.png"), full_page=True)

        self.page.set_viewport_size({"width": 390, "height": 844})
        self._assert_no_horizontal_overflow("cuts_active_mobile")
        self.page.screenshot(path=str(self.shot_dir / "cuts_active_mobile.png"), full_page=True)

        # 確定送信して DB に cuts[] が保存されること
        self.page.set_viewport_size({"width": 1280, "height": 900})
        self.page.get_by_test_id("recording-submit-btn").click()
        expect(self.page.locator("#recording-submit-success")).to_be_visible()

        order = RecordingEditOrder.objects.get(review_item=self.review_item)
        self.assertEqual(len(order.edl_snapshot["cuts"]), 2)
        self.assertEqual(order.edl_snapshot["cuts"][0]["action"], "accept")
        self.assertEqual(order.edl_snapshot["cuts"][1]["source"], "staff")
        self.assertEqual(order.edl_snapshot["cuts"][1]["label"], "マイク再接続済み")
        self.assertEqual(self.page_errors, [])
