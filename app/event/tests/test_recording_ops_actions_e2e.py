"""録画編集キュー再試行・期限切れ表示・緊急停止操作（#956）の実ブラウザ E2E テスト。"""
from __future__ import annotations

import os
import uuid
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless
from unittest.mock import MagicMock, patch

from django.test import override_settings, tag
from django.urls import reverse
from django.utils import timezone

from community.constants import RecordingPolicy
from event.models import RecordingEditOrder, RecordingReviewItem
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


def _valid_edl(*, max_sec: float = 120.0) -> dict:
    return {
        "schema_version": 1,
        "safe_bounds": {"min_sec": 0.0, "max_sec": max_sec},
        "trim": {"start_sec": 5.0, "end_sec": 95.0},
        "boundary_candidates": {
            "start": [{"sec": 5.0, "label": "挨拶開始"}],
            "end": [{"sec": 95.0, "label": "質疑終了"}],
        },
        "cuts": [],
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
class RecordingOpsActionsE2ETests(PlaywrightLiveServerTestCase):
    """PC (1280px) とスマホ (390px) で要対応集約・期限切れ表示・緊急停止パネルを検証する。"""

    def setUp(self) -> None:
        super().setUp()
        self.password = f"e2e-{uuid.uuid4().hex[:12]}"
        self.shot_dir = Path(
            os.environ.get("RECORDING_OPS_SCREENSHOT_DIR")
            or (Path(__file__).resolve().parents[3] / "test-results" / "e2e" / "recording_ops_actions")
        )
        self.shot_dir.mkdir(parents=True, exist_ok=True)
        self.page_errors: list[str] = []
        self.page.on("pageerror", lambda exc: self.page_errors.append(str(exc)))

        self.staff = make_discord_linked_user(
            user_name="e2e_ops_staff",
            email="e2e-ops-staff@example.test",
            password=self.password,
            is_staff=True,
        )
        owner = make_discord_linked_user(
            user_name="e2e_ops_owner",
            email="e2e-ops-owner@example.test",
            password=self.password,
        )
        self.community = make_community(name="E2E録画運用集会", owner=owner, recording_allowed=True)
        self.event = make_event(self.community)
        now = timezone.now()

        self.failed_detail = make_event_detail(
            self.event,
            status="approved",
            speaker="登壇者Fail",
            theme="書き出し失敗からの再試行検証",
            recording_policy=RecordingPolicy.PUBLIC,
        )
        self.failed_item = RecordingReviewItem.objects.create(
            event_detail=self.failed_detail,
            proxy_r2_key=f"recording-proxy/{self.failed_detail.pk}/{uuid.uuid4().hex}.mp4",
            proxy_duration_sec=120.0,
            proxy_uploaded_at=now - timedelta(hours=2),
            proxy_expires_at=now + timedelta(days=20),
            review_status=RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
            edl_revision=2,
            edl_json=_valid_edl(),
        )
        self.failed_order = RecordingEditOrder.objects.create(
            review_item=self.failed_item,
            event_detail=self.failed_detail,
            order_type=RecordingEditOrder.OrderType.APPLY_EDL,
            base_revision=1,
            target_revision=2,
            edl_snapshot=_valid_edl(),
            status=RecordingEditOrder.Status.FAILED,
            error_message="ffmpeg exited with code 1 during concat",
            active_lock_key=None,
        )

        self.stalled_detail = make_event_detail(
            self.event,
            status="approved",
            speaker="登壇者Stall",
            theme="滞留中の録画オーダー監視検証",
            recording_policy=RecordingPolicy.PUBLIC,
        )
        self.stalled_item = RecordingReviewItem.objects.create(
            event_detail=self.stalled_detail,
            proxy_r2_key=f"recording-proxy/{self.stalled_detail.pk}/{uuid.uuid4().hex}.mp4",
            proxy_duration_sec=120.0,
            proxy_uploaded_at=now - timedelta(hours=3),
            proxy_expires_at=now + timedelta(days=20),
            review_status=RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
            edl_revision=2,
            edl_json=_valid_edl(),
        )
        stalled_order = RecordingEditOrder.objects.create(
            review_item=self.stalled_item,
            event_detail=self.stalled_detail,
            order_type=RecordingEditOrder.OrderType.APPLY_EDL,
            base_revision=1,
            target_revision=2,
            edl_snapshot=_valid_edl(),
            status=RecordingEditOrder.Status.QUEUED,
            active_lock_key=f"item:{self.stalled_item.pk}",
        )
        RecordingEditOrder.objects.filter(pk=stalled_order.pk).update(created_at=now - timedelta(minutes=45))

        self.expired_detail = make_event_detail(
            self.event,
            status="approved",
            speaker="登壇者Expired",
            theme="公開30日経過後の期限切れプロキシ表示",
            recording_policy=RecordingPolicy.PUBLIC,
        )
        self.expired_item = RecordingReviewItem.objects.create(
            event_detail=self.expired_detail,
            proxy_r2_key="",
            proxy_duration_sec=120.0,
            proxy_uploaded_at=now - timedelta(days=31),
            proxy_expires_at=now - timedelta(days=1),
            review_status=RecordingReviewItem.ReviewStatus.EXPIRED,
            edl_revision=3,
            edl_json=_valid_edl(),
        )

        self.published_detail = make_event_detail(
            self.event,
            status="approved",
            speaker="登壇者Pub",
            theme="公開済みロック中の緊急停止検証",
            recording_policy=RecordingPolicy.PUBLIC,
            youtube_url="https://www.youtube.com/watch?v=pubVid00001",
        )
        self.published_item = RecordingReviewItem.objects.create(
            event_detail=self.published_detail,
            proxy_r2_key=f"recording-proxy/{self.published_detail.pk}/{uuid.uuid4().hex}.mp4",
            proxy_duration_sec=120.0,
            proxy_uploaded_at=now - timedelta(days=2),
            proxy_expires_at=now + timedelta(days=28),
            review_status=RecordingReviewItem.ReviewStatus.PUBLISHED,
            youtube_draft_video_id="pubVid00001",
            youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.PUBLIC,
            edl_revision=4,
            edl_json=_valid_edl(),
        )

    def _assert_no_horizontal_overflow(self, context_label: str) -> None:
        overflow = self.page.evaluate(
            "() => ({scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth})"
        )
        self.assertLessEqual(
            overflow["scrollWidth"],
            overflow["clientWidth"],
            f"Horizontal overflow detected in {context_label}: {overflow}",
        )

    @patch("event.services.recording_ops.storage.boto3.client")
    def test_retry_expired_and_emergency_stop_pc_and_mobile(self, mock_boto: MagicMock) -> None:
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = (
            "https://r2.example.invalid/vrc-ta-hub-private/sample.mp4?X-Amz-Signature=e2eops"
        )
        mock_boto.return_value = mock_s3

        self.login(self.staff.email, self.password)

        # 1. 一覧画面の「要対応」ボックス（失敗 + 滞留集約）
        list_url = f"{self.live_server_url}{reverse('event:recording_review_list')}"
        self.page.set_viewport_size({"width": 1280, "height": 900})
        self.page.goto(list_url)
        action_box = self.page.get_by_test_id("recording-action-required")
        expect(action_box).to_contain_text("要対応 2")
        expect(self.page.get_by_test_id("action-required-failed-row")).to_contain_text(
            "書き出し失敗からの再試行検証"
        )
        expect(self.page.get_by_test_id("action-required-stalled-row")).to_contain_text(
            "滞留中の録画オーダー監視検証"
        )
        self._assert_no_horizontal_overflow("ops_list_pc")
        self.page.screenshot(path=str(self.shot_dir / "ops_list_action_required_pc.png"), full_page=True)

        self.page.set_viewport_size({"width": 390, "height": 844})
        self._assert_no_horizontal_overflow("ops_list_mobile")
        self.page.screenshot(path=str(self.shot_dir / "ops_list_action_required_mobile.png"), full_page=True)

        # 2. プロキシ期限切れ（30日経過 R2 削除後）詳細画面
        expired_url = f"{self.live_server_url}{reverse('event:recording_review_detail', kwargs={'detail_id': self.expired_detail.pk})}"
        self.page.set_viewport_size({"width": 1280, "height": 900})
        self.page.goto(expired_url)
        state_card = self.page.get_by_test_id("proxy-state-card")
        expect(state_card).to_have_attribute("data-proxy-state", "expired")
        expect(state_card).to_contain_text("視聴期限（公開後 30 日）経過のためプロキシ動画は削除されました")
        expect(self.page.locator("#recording-proxy-player")).to_have_count(0)
        expect(self.page.get_by_test_id("recording-submit-btn")).to_be_disabled()
        self._assert_no_horizontal_overflow("ops_expired_pc")
        self.page.screenshot(path=str(self.shot_dir / "ops_detail_expired_pc.png"), full_page=True)

        self.page.set_viewport_size({"width": 390, "height": 844})
        self._assert_no_horizontal_overflow("ops_expired_mobile")
        self.page.screenshot(path=str(self.shot_dir / "ops_detail_expired_mobile.png"), full_page=True)

        # 3. 公開済みロック中の詳細画面 + confirm_panel.html 経由緊急停止
        pub_url = f"{self.live_server_url}{reverse('event:recording_review_detail', kwargs={'detail_id': self.published_detail.pk})}"
        self.page.set_viewport_size({"width": 1280, "height": 900})
        self.page.goto(pub_url)
        expect(self.page.get_by_test_id("public-locked-banner")).to_be_visible()
        expect(self.page.get_by_test_id("recording-submit-btn")).to_be_disabled()

        stop_trigger = self.page.get_by_test_id("emergency-stop-trigger")
        stop_panel = self.page.locator("#recording-stop-publish-panel")
        expect(stop_panel).to_be_hidden()
        stop_trigger.click()
        expect(stop_panel).to_be_visible()
        self._assert_no_horizontal_overflow("ops_stop_panel_pc")
        self.page.screenshot(path=str(self.shot_dir / "ops_detail_emergency_stop_pc.png"), full_page=True)

        self.page.set_viewport_size({"width": 390, "height": 844})
        self._assert_no_horizontal_overflow("ops_stop_panel_mobile")
        self.page.screenshot(path=str(self.shot_dir / "ops_detail_emergency_stop_mobile.png"), full_page=True)

        # 確認パネルの「緊急停止（非公開化）をキューへ登録する」をクリックして確定
        stop_panel.locator('button[type="submit"]').click()
        expect(self.page.get_by_test_id("active-order-banner")).to_be_visible()
        expect(self.page.get_by_test_id("active-order-banner")).to_contain_text("公開停止指示を処理中です")
        self.assertTrue(
            RecordingEditOrder.objects.filter(
                review_item=self.published_item,
                order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
                status=RecordingEditOrder.Status.QUEUED,
            ).exists()
        )

        # 4. 失敗オーダー詳細画面からの再試行実行
        fail_url = f"{self.live_server_url}{reverse('event:recording_review_detail', kwargs={'detail_id': self.failed_detail.pk})}"
        self.page.set_viewport_size({"width": 1280, "height": 900})
        self.page.goto(fail_url)
        expect(self.page.get_by_test_id("last-order-failed-banner")).to_be_visible()
        self.page.get_by_test_id("retry-failed-order-btn").click()
        expect(self.page.get_by_test_id("active-order-banner")).to_be_visible()
        self.assertTrue(
            RecordingEditOrder.objects.filter(
                review_item=self.failed_item,
                order_type=RecordingEditOrder.OrderType.APPLY_EDL,
                status=RecordingEditOrder.Status.QUEUED,
            ).exists()
        )
        self.assertEqual(self.page_errors, [])
