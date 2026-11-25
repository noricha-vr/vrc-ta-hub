"""運営スタッフ用 録画確認・開始終了トリミング UI の実ブラウザ E2E テスト。"""
from __future__ import annotations

import os
import re
import subprocess
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


def _build_sample_mp4(path: Path) -> bytes:
    """ffmpeg で小さなテスト用 MP4（15 秒）を生成してバイト列を返す。"""
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=0x1b7de6:s=320x180:d=15",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=mono:sample_rate=44100",
        "-t", "15", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "32k", str(path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return path.read_bytes()


def _valid_edl(*, start_sec: float = 2.0, end_sec: float = 12.0, max_sec: float = 15.0) -> dict:
    return {
        "schema_version": 1,
        "safe_bounds": {"min_sec": 0.0, "max_sec": max_sec},
        "trim": {"start_sec": start_sec, "end_sec": end_sec},
        "boundary_candidates": {
            "start": [{"sec": 2.0, "label": "挨拶開始"}, {"sec": 4.0, "label": "本題開始"}],
            "end": [{"sec": 12.0, "label": "質疑終了"}, {"sec": 13.5, "label": "拍手終了"}],
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
class RecordingReviewE2ETests(PlaywrightLiveServerTestCase):
    """PC（1280px）とスマホ（390px）での一覧・詳細・プレーヤー・競合・公開ロックを検証する。"""

    password = "e2e-recording-pass"
    mp4_bytes: bytes = b""
    tmp_dir: tempfile.TemporaryDirectory[str] | None = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp_dir = tempfile.TemporaryDirectory(prefix="vrc-ta-hub-rec-e2e-")
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
            os.environ.get("RECORDING_REVIEW_SCREENSHOT_DIR")
            or (Path(__file__).resolve().parents[3] / "test-results" / "e2e" / "recording_review")
        )
        self.shot_dir.mkdir(parents=True, exist_ok=True)
        self.page_errors: list[str] = []
        self.page.on("pageerror", lambda exc: self.page_errors.append(str(exc)))
        self.staff = make_discord_linked_user(
            user_name="e2e_rec_staff", email="e2e-rec-staff@example.test", password=self.password, is_staff=True,
        )
        owner = make_discord_linked_user(
            user_name="e2e_rec_owner", email="e2e-rec-owner@example.test", password=self.password,
        )
        self.community = make_community(name="E2E録画検証集会", owner=owner, recording_allowed=True)
        self.event = make_event(self.community)

        self.public_detail = make_event_detail(
            self.event, status="approved", speaker="登壇者A", theme="VRChat シェーダー入門と最適化",
            recording_policy=RecordingPolicy.PUBLIC,
        )
        self.locked_detail = make_event_detail(
            self.event, status="approved", speaker="登壇者B", theme="公開済みロック検証の発表",
            recording_policy=RecordingPolicy.PUBLIC,
        )
        self.allowed_detail = make_event_detail(
            self.event, status="approved", speaker="登壇者C", theme="旧設定で非公開固定の発表",
            recording_policy=RecordingPolicy.ALLOWED,
        )
        self.forbidden_detail = make_event_detail(
            self.event, status="approved", speaker="登壇者D", theme="ボット撮影しない設定の発表",
            recording_policy=RecordingPolicy.FORBIDDEN,
        )

        self.public_item = RecordingReviewItem.objects.create(
            event_detail=self.public_detail,
            proxy_r2_key=f"recording-proxy/{self.public_detail.pk}/{uuid.uuid4().hex}.mp4",
            proxy_duration_sec=15.0,
            proxy_uploaded_at=timezone.now(),
            proxy_expires_at=timezone.now() + timedelta(days=7),
            review_status=RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
            edl_revision=1,
            edl_json=_valid_edl(),
        )
        self.locked_item = RecordingReviewItem.objects.create(
            event_detail=self.locked_detail,
            proxy_r2_key=f"recording-proxy/{self.locked_detail.pk}/{uuid.uuid4().hex}.mp4",
            proxy_duration_sec=15.0,
            proxy_uploaded_at=timezone.now(),
            proxy_expires_at=timezone.now() + timedelta(days=7),
            review_status=RecordingReviewItem.ReviewStatus.PUBLISHED,
            youtube_draft_video_id="dQw4w9WgXcQ",
            youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.PUBLIC,
            edl_revision=2,
            edl_json=_valid_edl(),
        )
        self.page.route("https://r2.example.invalid/**", self._fulfill_mock_mp4)

    def _fulfill_mock_mp4(self, route: Any) -> None:
        total = len(self.mp4_bytes)
        range_header = route.request.headers.get("range", "")
        if range_header.startswith("bytes="):
            spec = range_header.replace("bytes=", "", 1).split(",")[0].strip()
            start_str, _, end_str = spec.partition("-")
            start = int(start_str) if start_str else 0
            end = int(end_str) if end_str else total - 1
            end = min(end, total - 1)
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
    def test_recording_review_ui_pc_and_mobile_end_to_end(self, mock_boto: MagicMock) -> None:
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://r2.example.invalid/vrc-ta-hub-private/sample.mp4?X-Amz-Signature=e2esig"
        mock_boto.return_value = mock_s3

        self.login(self.staff.email, self.password)

        # 1. 一覧画面（PC 1280px）
        list_url = f"{self.live_server_url}{reverse('event:recording_review_list')}"
        self.page.set_viewport_size({"width": 1280, "height": 900})
        self.page.goto(list_url)
        expect(self.page.get_by_role("heading", name=re.compile(r"^録画・YouTube管理"))).to_be_visible()
        expect(self.page.get_by_test_id("recording-action-required")).to_contain_text("要対応 1")
        self._assert_no_horizontal_overflow("list_pc")
        self.page.screenshot(path=str(self.shot_dir / "list_pc.png"), full_page=True)

        # 一覧画面（スマホ 390px）
        self.page.set_viewport_size({"width": 390, "height": 844})
        self._assert_no_horizontal_overflow("list_mobile")
        self.page.screenshot(path=str(self.shot_dir / "list_mobile.png"), full_page=True)

        # 2. 空状態（PC 1280px）
        self.page.set_viewport_size({"width": 1280, "height": 900})
        self.page.goto(f"{list_url}?q=該当しない検索キーワード999")
        expect(self.page.get_by_test_id("recording-empty-state")).to_contain_text("条件に一致する発表はありません。")
        self._assert_no_horizontal_overflow("list_empty_pc")
        self.page.screenshot(path=str(self.shot_dir / "list_empty_pc.png"), full_page=True)

        # 詳細・トリミング画面（PC 1280px）
        detail_url = f"{self.live_server_url}{reverse('event:recording_review_detail', kwargs={'detail_id': self.public_detail.pk})}"
        self.page.goto(detail_url)
        self.page.wait_for_function(
            "() => { const v = document.getElementById('recording-proxy-player'); return v && v.readyState >= 1; }"
        )
        expect(self.page.locator("#recording-proxy-player")).to_be_visible()
        # 初期状態（preview モード、start_sec=2.0）でタイムライン・時刻表示・モードバナーが初期化されていること
        expect(self.page.get_by_test_id("recording-mode-badge")).to_have_text("プレビュー中")
        expect(self.page.locator("#id_start_sec")).to_have_value("0:02.0")
        expect(self.page.locator("#id_end_sec")).to_have_value("0:12.0")
        expect(self.page.locator("#recording-timeline-bar .recording-tl-keep")).to_have_count(1)
        expect(self.page.locator("#recording-timeline-bar .recording-tl-margin")).to_have_count(2)
        expect(self.page.locator("#recording-timeline-bar .recording-tl-handle")).to_have_count(2)
        expect(self.page.get_by_test_id("timeline-cand-start")).to_have_count(2)
        expect(self.page.get_by_test_id("timeline-cand-end")).to_have_count(2)
        self.page.wait_for_function(
            "() => { const v = document.getElementById('recording-proxy-player'); return v && v.currentTime >= 1.9; }"
        )

        # 候補チップ・時刻直入力（M:SS.s & 秒）・±1秒/±5秒微調整が無限再帰しないこと（1操作で change 発火は最大1回）
        self.page.evaluate(
            """() => {
                window.__startChangeCount = 0;
                const el = document.getElementById('id_start_sec_raw');
                el.addEventListener('change', () => { window.__startChangeCount += 1; });
            }"""
        )
        self.page.locator('[data-testid="start-candidate-chip"][data-candidate-sec="4"]').click()
        expect(self.page.locator("#id_start_sec_raw")).to_have_value("4")
        expect(self.page.locator("#id_start_sec")).to_have_value("0:04.0")
        self.assertEqual(self.page.evaluate("() => window.__startChangeCount"), 1)
        expect(self.page.locator('[data-admin-form-status="recording-trim-form"]')).to_contain_text("未保存の変更があります")
        self.page.evaluate("() => document.getElementById('recording-proxy-player').pause()")

        # 秒数形式入力
        self.page.locator("#id_start_sec").fill("2.5")
        self.page.locator("#id_start_sec").press("Tab")
        expect(self.page.locator("#id_start_sec_raw")).to_have_value("2.5")
        expect(self.page.locator("#id_start_sec")).to_have_value("0:02.5")
        self.assertEqual(self.page.evaluate("() => window.__startChangeCount"), 2)

        # M:SS.s 形式入力
        self.page.locator("#id_start_sec").fill("0:03.5")
        self.page.locator("#id_start_sec").press("Enter")
        expect(self.page.locator("#id_start_sec_raw")).to_have_value("3.5")
        expect(self.page.locator("#id_start_sec")).to_have_value("0:03.5")
        self.assertEqual(self.page.evaluate("() => window.__startChangeCount"), 3)

        # ±1秒・±5秒微調整
        self.page.locator('[data-adjust-target="start"][data-adjust-delta="-1"]').click()
        expect(self.page.locator("#id_start_sec_raw")).to_have_value("2.5")
        self.assertEqual(self.page.evaluate("() => window.__startChangeCount"), 4)

        self.page.locator('[data-adjust-target="start"][data-adjust-delta="-5"]').click()
        expect(self.page.locator("#id_start_sec_raw")).to_have_value("0")  # safe_bounds.min_sec (0) にクランプ
        self.assertEqual(self.page.evaluate("() => window.__startChangeCount"), 5)

        # 終了確認秒数プルダウン（5/7/10秒前）＋ [終了前を再生]
        self.page.get_by_test_id("end-preview-offset-select").select_option("10")
        expect(self.page.get_by_test_id("play-before-end-btn")).to_have_text("終了前を再生")
        self.page.get_by_test_id("play-before-end-btn").click()
        expect(self.page.get_by_test_id("recording-playback-feedback")).to_contain_text("10 秒前")
        self.page.wait_for_function(
            "() => { const v = document.getElementById('recording-proxy-player'); return v && v.currentTime >= 1.8 && v.currentTime <= 4.0; }"
        )

        # モード切替（「余白込み全体」↔「トリミング後」）と開始/終了範囲クランプの実動作
        self.page.locator('input[name="playback_mode"][value="full"]').check(force=True)
        self.page.locator('input[name="playback_mode"][value="preview"]').check(force=True)
        # プレビューモードで開始位置(2s -> 4s)変更時と、開始位置より前へのシーク時に 4s 以上へ戻ることを検証
        self.page.locator('[data-testid="start-candidate-chip"][data-candidate-sec="2"]').click()
        self.page.evaluate("() => { const v = document.getElementById('recording-proxy-player'); v.currentTime = 2.5; v.play(); }")
        self.page.locator('[data-testid="start-candidate-chip"][data-candidate-sec="4"]').click()
        self.page.wait_for_function(
            "() => { const v = document.getElementById('recording-proxy-player'); return v && v.currentTime >= 3.9; }"
        )
        self.page.evaluate("() => { const v = document.getElementById('recording-proxy-player'); v.currentTime = 1.0; }")
        self.page.wait_for_function(
            "() => { const v = document.getElementById('recording-proxy-player'); return v && v.currentTime >= 3.9; }"
        )
        self.page.locator('[data-testid="start-candidate-chip"][data-candidate-sec="2"]').click()
        self.page.evaluate("() => { const v = document.getElementById('recording-proxy-player'); v.currentTime = 11.7; v.play(); }")
        self.page.wait_for_function(
            "() => { const v = document.getElementById('recording-proxy-player'); return v && v.paused && Math.abs(v.currentTime - 12.0) < 0.5; }"
        )
        # トラック上の終了候補マーカークリック（13.5s 適用＆確認再生）＋区間再生中の延長動作
        self.page.locator('input[name="playback_mode"][value="full"]').check(force=True)
        self.page.get_by_test_id("play-before-end-btn").click()
        self.page.evaluate("() => { const v = document.getElementById('recording-proxy-player'); v.currentTime = 11.5; v.play(); }")
        self.page.locator('[data-testid="timeline-cand-end"][data-candidate-sec="13.5"]').click()
        expect(self.page.locator("#id_end_sec_raw")).to_have_value("13.5")
        self.page.evaluate("() => { const v = document.getElementById('recording-proxy-player'); v.currentTime = 11.5; v.play(); }")
        self.page.wait_for_function(
            "() => { const v = document.getElementById('recording-proxy-player'); return v && v.currentTime > 12.2 && !v.paused; }"
        )
        self.page.wait_for_function(
            "() => { const v = document.getElementById('recording-proxy-player'); return v && v.paused && Math.abs(v.currentTime - 13.5) < 0.6; }"
        )
        self.page.locator('[data-testid="end-candidate-chip"][data-candidate-sec="12"]').click()
        self.page.evaluate("() => document.getElementById('recording-proxy-player').pause()")
        self.page.locator('input[name="playback_mode"][value="preview"]').check(force=True)
        self.page.locator('[data-adjust-target="start"][data-adjust-delta="-5"]').click()
        expect(self.page.locator("#id_start_sec_raw")).to_have_value("0")
        # [ここから再生] で start_sec (0s) 付近へシークされること
        self.page.get_by_test_id("play-from-start-btn").click()
        self.page.wait_for_function(
            "() => { const v = document.getElementById('recording-proxy-player'); return v && v.currentTime < 1.5; }"
        )
        self.page.evaluate("() => document.getElementById('recording-proxy-player').pause()")

        # タイムラインハンドル（開始・終了）・再生ヘッドの Pointer ドラッグ操作（ツールチップ＆採用時間更新）
        bar_box = self.page.get_by_test_id("recording-timeline-bar").bounding_box()
        self.assertIsNotNone(bar_box)
        start_handle_box = self.page.get_by_test_id("timeline-handle-start").bounding_box()
        self.assertIsNotNone(start_handle_box)
        hx = start_handle_box["x"] + start_handle_box["width"] / 2
        hy = start_handle_box["y"] + start_handle_box["height"] / 2
        target_x = bar_box["x"] + bar_box["width"] * (3.0 / 15.0)
        self.page.mouse.move(hx, hy)
        self.page.mouse.down()
        self.page.mouse.move(target_x, hy, steps=5)
        expect(self.page.get_by_test_id("timeline-drag-tooltip")).to_be_visible()
        expect(self.page.get_by_test_id("timeline-drag-tooltip")).to_contain_text("開始")
        expect(self.page.locator("#recording-adopted-duration")).to_contain_text("9.0秒")
        moved_handle_box = self.page.get_by_test_id("timeline-handle-start").bounding_box()
        self.assertIsNotNone(moved_handle_box)
        self.assertGreater(moved_handle_box["x"], start_handle_box["x"] + 20)
        self.page.mouse.up()
        expect(self.page.get_by_test_id("timeline-drag-tooltip")).to_be_hidden()
        self.assertAlmostEqual(float(self.page.locator("#id_start_sec_raw").input_value()), 3.0, delta=0.4)

        end_handle_box = self.page.get_by_test_id("timeline-handle-end").bounding_box()
        self.assertIsNotNone(end_handle_box)
        ex = end_handle_box["x"] + end_handle_box["width"] / 2
        ey = end_handle_box["y"] + end_handle_box["height"] / 2
        end_target_x = bar_box["x"] + bar_box["width"] * (10.0 / 15.0)
        self.page.mouse.move(ex, ey)
        self.page.mouse.down()
        self.page.mouse.move(end_target_x, ey, steps=5)
        expect(self.page.get_by_test_id("timeline-drag-tooltip")).to_be_visible()
        expect(self.page.get_by_test_id("timeline-drag-tooltip")).to_contain_text("終了")
        expect(self.page.locator("#recording-adopted-duration")).to_contain_text("7.0秒")
        self.page.mouse.up()
        expect(self.page.get_by_test_id("timeline-drag-tooltip")).to_be_hidden()
        self.assertAlmostEqual(float(self.page.locator("#id_end_sec_raw").input_value()), 10.0, delta=0.4)
        self.page.locator('[data-testid="end-candidate-chip"][data-candidate-sec="12"]').click()
        self.page.evaluate("() => document.getElementById('recording-proxy-player').pause()")

        # モード切替表示（「全体表示中」↔「プレビュー中」）と [現在位置を開始にする] / [現在位置を終了にする]
        self.page.locator('input[name="playback_mode"][value="full"]').check(force=True)
        expect(self.page.get_by_test_id("recording-mode-badge")).to_have_text("全体表示中")
        expect(self.page.get_by_test_id("recording-timeline-bar")).to_have_class(re.compile(r"\bis-full-mode\b"))
        # 再生ヘッドドラッグで位置移動
        head_box = self.page.locator(".recording-tl-playhead-knob").bounding_box()
        self.assertIsNotNone(head_box)
        px = head_box["x"] + head_box["width"] / 2
        py = head_box["y"] + head_box["height"] / 2
        self.page.mouse.move(px, py)
        self.page.mouse.down()
        self.page.mouse.move(bar_box["x"] + bar_box["width"] * (6.0 / 15.0), py, steps=4)
        expect(self.page.get_by_test_id("timeline-drag-tooltip")).to_contain_text("現在")
        self.page.mouse.up()
        self.page.wait_for_function(
            "() => { const v = document.getElementById('recording-proxy-player'); return v && Math.abs(v.currentTime - 6.0) < 0.8; }"
        )

        self.page.evaluate("() => { const v = document.getElementById('recording-proxy-player'); v.currentTime = 3.2; }")
        self.page.wait_for_function(
            "() => { const v = document.getElementById('recording-proxy-player'); return v && Math.abs(v.currentTime - 3.2) < 0.2; }"
        )
        self.page.get_by_test_id("set-start-from-player-btn").click()
        expect(self.page.locator("#id_start_sec_raw")).to_have_value("3.2")
        expect(self.page.locator("#id_start_sec")).to_have_value("0:03.2")
        self.page.evaluate("() => { const v = document.getElementById('recording-proxy-player'); v.currentTime = 11.4; }")
        self.page.wait_for_function(
            "() => { const v = document.getElementById('recording-proxy-player'); return v && Math.abs(v.currentTime - 11.4) < 0.2; }"
        )
        self.page.get_by_test_id("set-end-from-player-btn").click()
        expect(self.page.locator("#id_end_sec_raw")).to_have_value("11.4")
        expect(self.page.locator("#id_end_sec")).to_have_value("0:11.4")
        # 余白位置 (1.0s) でプレビューモードに戻すと start_sec (3.2s) へ自動移動すること
        self.page.evaluate("() => { const v = document.getElementById('recording-proxy-player'); v.currentTime = 1.0; }")
        self.page.locator('input[name="playback_mode"][value="preview"]').check(force=True)
        expect(self.page.get_by_test_id("recording-mode-badge")).to_have_text("プレビュー中")
        self.page.wait_for_function(
            "() => { const v = document.getElementById('recording-proxy-player'); return v && v.currentTime >= 3.1; }"
        )
        self.page.locator('[data-testid="start-candidate-chip"][data-candidate-sec="2"]').click()
        self.page.locator('[data-testid="end-candidate-chip"][data-candidate-sec="12"]').click()
        self.page.evaluate("() => document.getElementById('recording-proxy-player').pause()")

        self._assert_no_horizontal_overflow("detail_pc")
        self.page.screenshot(path=str(self.shot_dir / "detail_pc.png"), full_page=True)

        # 詳細画面（スマホ 390px）
        self.page.set_viewport_size({"width": 390, "height": 844})
        self._assert_no_horizontal_overflow("detail_mobile")
        self.page.screenshot(path=str(self.shot_dir / "detail_mobile.png"), full_page=True)

        # 4. 409 Conflict 検証（別画面・別スタッフが先に revision を進めたと仮定）
        self.page.set_viewport_size({"width": 1280, "height": 900})
        self.public_item.edl_revision = 9
        self.public_item.save(update_fields=["edl_revision"])
        self.page.locator("#id_start_sec").fill("3")
        self.page.locator("#id_start_sec").press("Tab")
        self.page.locator("#id_end_sec").fill("11")
        self.page.locator("#id_end_sec").press("Tab")
        self.page.get_by_test_id("recording-submit-btn").click()
        expect(self.page.get_by_test_id("recording-conflict-banner")).to_be_visible()
        expect(self.page.get_by_test_id("reload-latest-btn")).to_be_visible()
        expect(self.page.get_by_test_id("recording-submit-btn")).to_be_disabled()
        expect(self.page.locator("#id_start_sec")).to_be_disabled()
        expect(self.page.locator("#id_expected_revision")).to_have_value("")
        self._assert_no_horizontal_overflow("detail_conflict_pc")
        self.page.screenshot(path=str(self.shot_dir / "detail_conflict_pc.png"), full_page=True)

        # 最新状態を再読み込みして正常に確定（queued）できることを確認
        self.page.get_by_test_id("reload-latest-btn").click()
        self.page.wait_for_function(
            "() => { const v = document.getElementById('recording-proxy-player'); return v && v.readyState >= 1; }"
        )
        self.page.locator("#id_start_sec").fill("3")
        self.page.locator("#id_start_sec").press("Tab")
        self.page.locator("#id_end_sec").fill("11")
        self.page.locator("#id_end_sec").press("Tab")
        self.page.get_by_test_id("recording-submit-btn").click()
        expect(self.page.locator("#recording-submit-success")).to_be_visible()
        expect(self.page.get_by_test_id("recording-submit-btn")).to_be_disabled()
        self.assertTrue(
            RecordingEditOrder.objects.filter(review_item=self.public_item, status=RecordingEditOrder.Status.QUEUED).exists()
        )

        # 5. 公開済みロック画面（PC 1280px）
        locked_url = f"{self.live_server_url}{reverse('event:recording_review_detail', kwargs={'detail_id': self.locked_detail.pk})}"
        self.page.goto(locked_url)
        expect(self.page.get_by_test_id("public-locked-banner")).to_be_visible()
        expect(self.page.locator("#id_start_sec")).to_be_disabled()
        expect(self.page.locator("#id_end_sec")).to_be_disabled()
        expect(self.page.get_by_test_id("timeline-handle-start")).to_be_disabled()
        expect(self.page.get_by_test_id("timeline-handle-end")).to_be_disabled()
        expect(self.page.get_by_test_id("recording-submit-btn")).to_be_disabled()
        self._assert_no_horizontal_overflow("detail_locked_pc")
        self.page.screenshot(path=str(self.shot_dir / "detail_locked_pc.png"), full_page=True)
        self.assertEqual(self.page_errors, [])

    @patch("event.services.recording_ops.storage.boto3.client")
    def test_long_recording_zoom_fixed_window_drag_and_empty_candidates(self, mock_boto: MagicMock) -> None:
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://r2.example.invalid/vrc-ta-hub-private/long.mp4?X-Amz-Signature=e2elong"
        mock_boto.return_value = mock_s3

        long_detail = make_event_detail(
            self.event, status="approved", speaker="登壇者Long", theme="90分級録画の拡大タイムライン検証",
            recording_policy=RecordingPolicy.PUBLIC,
        )
        RecordingReviewItem.objects.create(
            event_detail=long_detail,
            proxy_r2_key=f"recording-proxy/{long_detail.pk}/{uuid.uuid4().hex}.mp4",
            proxy_duration_sec=5577.0,
            proxy_uploaded_at=timezone.now(),
            proxy_expires_at=timezone.now() + timedelta(days=7),
            review_status=RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
            edl_revision=1,
            edl_json={
                "schema_version": 1,
                "safe_bounds": {"min_sec": 0.0, "max_sec": 5577.0},
                "trim": {"start_sec": 195.8, "end_sec": 5577.0},
                "boundary_candidates": {"start": [], "end": []},
                "cuts": [],
            },
        )

        self.login(self.staff.email, self.password)
        long_url = f"{self.live_server_url}{reverse('event:recording_review_detail', kwargs={'detail_id': long_detail.pk})}"
        self.page.set_viewport_size({"width": 1280, "height": 900})
        self.page.goto(long_url)
        self.page.wait_for_function(
            "() => { const v = document.getElementById('recording-proxy-player'); return v && v.readyState >= 1; }"
        )

        # 候補ゼロ時は候補チップ行が出ないこと
        expect(self.page.get_by_test_id("start-candidates-row")).to_have_count(0)
        expect(self.page.get_by_test_id("end-candidates-row")).to_have_count(0)
        expect(self.page.locator("#id_start_sec")).to_have_value("3:15.8")
        expect(self.page.locator("#id_end_sec")).to_have_value("1:32:57.0")
        expect(self.page.get_by_test_id("timeline-axis-min")).to_have_text("0:00")
        expect(self.page.get_by_test_id("timeline-axis-max")).to_have_text("1:32:57")

        # [開始付近 ±2分] で表示窓が固定され、ドラッグ中も窓が逃げないこと
        self.page.get_by_test_id("zoom-btn-start").click()
        expect(self.page.get_by_test_id("zoom-btn-start")).to_have_attribute("aria-pressed", "true")
        axis_min_before = self.page.get_by_test_id("timeline-axis-min").inner_text()
        axis_max_before = self.page.get_by_test_id("timeline-axis-max").inner_text()
        self.assertEqual(axis_min_before, "1:16")
        self.assertEqual(axis_max_before, "5:16")
        expect(self.page.get_by_test_id("timeline-handle-end")).to_be_hidden()

        start_box = self.page.get_by_test_id("timeline-handle-start").bounding_box()
        bar_box = self.page.get_by_test_id("recording-timeline-bar").bounding_box()
        self.assertIsNotNone(start_box)
        self.assertIsNotNone(bar_box)
        hx = start_box["x"] + start_box["width"] / 2
        hy = start_box["y"] + start_box["height"] / 2
        self.page.mouse.move(hx, hy)
        self.page.mouse.down()
        self.page.mouse.move(hx + 60, hy, steps=5)
        expect(self.page.get_by_test_id("timeline-drag-tooltip")).to_be_visible()
        # ドラッグ中も表示窓は固定されたまま
        self.assertEqual(self.page.get_by_test_id("timeline-axis-min").inner_text(), axis_min_before)
        self.assertEqual(self.page.get_by_test_id("timeline-axis-max").inner_text(), axis_max_before)
        self.page.mouse.up()
        self.assertEqual(self.page.get_by_test_id("timeline-axis-min").inner_text(), axis_min_before)
        self.assertGreater(float(self.page.locator("#id_start_sec_raw").input_value()), 200.0)

        # [終了付近 ±2分] で終了ハンドルが見えること
        self.page.get_by_test_id("zoom-btn-end").click()
        expect(self.page.get_by_test_id("timeline-handle-end")).to_be_visible()
        expect(self.page.get_by_test_id("timeline-axis-max")).to_have_text("1:32:57")
        self.page.get_by_test_id("zoom-btn-all").click()
        expect(self.page.get_by_test_id("timeline-axis-min")).to_have_text("0:00")
        self._assert_no_horizontal_overflow("detail_long_pc")
        self.assertEqual(self.page_errors, [])
