"""録画運用ダッシュボード画面・記事ステータス連携の Playwright 実ブラウザ E2E テスト。"""
from __future__ import annotations

import json
import os
import uuid
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless

from django.test import override_settings, tag
from django.urls import reverse
from django.utils import timezone

from community.constants import RecordingPolicy
from event.models import EventDetail, RecordingOpsSnapshot, RecordingReviewItem
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

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "api_v1" / "tests" / "fixtures" / "recording_ops_snapshot_v1.json"


@tag("e2e", "browser")
@skipUnless(PLAYWRIGHT_AVAILABLE, "Playwright E2E dependencies are not installed")
@override_settings(
    DEBUG_LOGIN_SKIP=False,
    DISCORD_AUTH_REQUIRED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class RecordingOpsDashboardE2ETests(PlaywrightLiveServerTestCase):
    """PC（1280px）とスマホ（390px）でのダッシュボード描画・期間切替・空/stale・一覧連携を検証する。"""

    password = "e2e-dashboard-pass"

    def setUp(self) -> None:
        super().setUp()
        self.shot_dir = Path(
            os.environ.get("DASHBOARD_SCREENSHOT_DIR")
            or (Path(__file__).resolve().parents[3] / "test-results" / "e2e" / "recording_ops_dashboard")
        )
        self.shot_dir.mkdir(parents=True, exist_ok=True)
        self.page_errors: list[str] = []
        self.page.on("pageerror", lambda exc: self.page_errors.append(str(exc)))

        self.staff = make_discord_linked_user(
            user_name="e2e_dash_staff",
            email="e2e-dash-staff@example.test",
            password=self.password,
            is_staff=True,
            is_superuser=True,
        )
        owner = make_discord_linked_user(
            user_name="e2e_dash_owner",
            email="e2e-dash-owner@example.test",
            password=self.password,
        )
        self.community = make_community(name="E2Eダッシュボード検証集会", owner=owner, recording_allowed=True)
        self.event = make_event(self.community)

        self.pub_detail = make_event_detail(
            self.event, status="approved", speaker="登壇者A", theme="シェーダー最適化入門",
            recording_policy=RecordingPolicy.PUBLIC,
            h1="シェーダー最適化入門", contents="公開された記事本文です。",
        )
        self.wait_detail = make_event_detail(
            self.event, status="approved", speaker="登壇者B", theme="オーディオ最適化の設計",
            recording_policy=RecordingPolicy.PUBLIC,
            article_consent=EventDetail.ArticleConsent.OK,
            article_generation_requested_at=timezone.now(),
        )
        self.allowed_detail = make_event_detail(
            self.event, status="approved", speaker="登壇者C", theme="旧設定で非公開固定の発表",
            recording_policy=RecordingPolicy.ALLOWED,
        )
        self.forbidden_detail = make_event_detail(
            self.event, status="approved", speaker="登壇者D", theme="非配信かつ記事NGの発表",
            recording_policy=RecordingPolicy.FORBIDDEN,
            article_consent=EventDetail.ArticleConsent.NG,
        )
        RecordingReviewItem.objects.create(
            event_detail=self.pub_detail,
            proxy_r2_key=f"recording-proxy/{self.pub_detail.pk}/{uuid.uuid4().hex}.mp4",
            proxy_duration_sec=180.0,
            proxy_uploaded_at=timezone.now(),
            proxy_expires_at=timezone.now() + timedelta(days=7),
            review_status=RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
        )

    def _assert_no_horizontal_overflow(self, label: str) -> None:
        overflow = self.page.evaluate(
            "() => ({scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth})"
        )
        self.assertLessEqual(
            overflow["scrollWidth"],
            overflow["clientWidth"],
            f"Horizontal overflow in {label}: {overflow}",
        )

    def test_dashboard_states_and_list_integration_pc_and_mobile(self) -> None:
        self.login(self.staff.email, self.password)
        dashboard_url = f"{self.live_server_url}{reverse('event:recording_ops_dashboard')}"
        list_url = f"{self.live_server_url}{reverse('event:recording_review_list')}"

        # 1. スナップショット未登録時の empty_state（PC 1280px）
        self.page.set_viewport_size({"width": 1280, "height": 900})
        self.page.goto(dashboard_url)
        expect(self.page.get_by_test_id("snapshot-empty-state")).to_be_visible()
        self._assert_no_horizontal_overflow("dashboard_empty_pc")
        self.page.screenshot(path=str(self.shot_dir / "dashboard_empty_pc.png"), full_page=True)

        # 2. スナップショット登録後（通常状態 + 7/30/90日切替 + 記事ステータス連携）
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        payload["action_items"][0]["detail_id"] = self.pub_detail.pk
        payload["action_items"][0]["community_name"] = self.community.name
        payload["action_items"][0]["title"] = self.pub_detail.theme
        payload["upcoming_schedule"][0]["community_name"] = self.community.name
        payload["upcoming_schedule"][0]["talk_count"] = 4
        payload["upcoming_schedule"][0]["talks"] = [
            {"detail_id": self.pub_detail.pk, "speaker": "登壇者A", "theme": self.pub_detail.theme, "recording_policy": "public"},
            {"detail_id": self.wait_detail.pk, "speaker": "登壇者B", "theme": self.wait_detail.theme, "recording_policy": "public"},
            {"detail_id": self.allowed_detail.pk, "speaker": "登壇者C", "theme": self.allowed_detail.theme, "recording_policy": "allowed"},
            {"detail_id": self.forbidden_detail.pk, "speaker": "登壇者D", "theme": self.forbidden_detail.theme, "recording_policy": "forbidden"},
        ]
        snapshot = RecordingOpsSnapshot.objects.create(
            schema_version=1,
            snapshot_at=timezone.now() - timedelta(minutes=20),
            payload=payload,
        )

        self.page.goto(dashboard_url)
        expect(self.page.get_by_test_id("dashboard-next-session")).to_be_visible()
        expect(self.page.get_by_test_id("dashboard-action-required")).to_contain_text("要対応 1")
        expect(self.page.get_by_test_id("funnel-metrics")).to_have_attribute("data-active-period", "30")
        self.page.get_by_test_id("funnel-period-btn-7").click()
        expect(self.page.get_by_test_id("funnel-metrics")).to_have_attribute("data-active-period", "7")
        self.page.get_by_test_id("funnel-period-btn-90").click()
        expect(self.page.get_by_test_id("funnel-metrics")).to_have_attribute("data-active-period", "90")
        expect(self.page.get_by_test_id("dashboard-venue-coverage")).to_be_visible()
        expect(self.page.get_by_test_id("dashboard-upcoming-schedule")).to_contain_text("記事: 公開済み")
        expect(self.page.get_by_test_id("dashboard-upcoming-schedule")).to_contain_text("記事: 下書き（生成待ち）")
        expect(self.page.get_by_test_id("dashboard-upcoming-schedule")).to_contain_text("記事: 未作成")
        expect(self.page.get_by_test_id("dashboard-upcoming-schedule")).to_contain_text("記事: 記事化 NG")
        self._assert_no_horizontal_overflow("dashboard_pc")
        self.page.screenshot(path=str(self.shot_dir / "dashboard_pc.png"), full_page=True)

        # スマホ幅（390px）
        self.page.set_viewport_size({"width": 390, "height": 844})
        self._assert_no_horizontal_overflow("dashboard_mobile")
        self.page.screenshot(path=str(self.shot_dir / "dashboard_mobile.png"), full_page=True)

        # 3. 鮮度警告 (stale) 表示（PC 1280px）
        snapshot.snapshot_at = timezone.now() - timedelta(hours=30)
        snapshot.save(update_fields=["snapshot_at"])
        self.page.set_viewport_size({"width": 1280, "height": 900})
        self.page.goto(dashboard_url)
        expect(self.page.get_by_test_id("snapshot-stale-warning")).to_be_visible()
        self._assert_no_horizontal_overflow("dashboard_stale_pc")
        self.page.screenshot(path=str(self.shot_dir / "dashboard_stale_pc.png"), full_page=True)

        # 4. 動画確認一覧（サブナビ遷移 ＋ 記事ステータス連携、PC & スマホ）
        self.page.get_by_test_id("recording-tab-list").click()
        expect(self.page).to_have_url(list_url)
        expect(self.page.locator('[data-testid="recording-article-chip"]').first).to_be_visible()
        self._assert_no_horizontal_overflow("list_article_pc")
        self.page.screenshot(path=str(self.shot_dir / "list_article_pc.png"), full_page=True)

        self.page.set_viewport_size({"width": 390, "height": 844})
        self._assert_no_horizontal_overflow("list_article_mobile")
        self.page.screenshot(path=str(self.shot_dir / "list_article_mobile.png"), full_page=True)
        self.assertEqual(self.page_errors, [])
