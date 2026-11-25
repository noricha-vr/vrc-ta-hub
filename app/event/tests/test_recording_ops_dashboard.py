"""録画運用ダッシュボード画面・記事ステータス連携の単体・統合テスト。"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from bs4 import BeautifulSoup
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from community.constants import RecordingPolicy
from community.models import CommunityMember
from event.models import EventDetail, RecordingOpsSnapshot, RecordingReviewItem
from event.services.recording_ops.dashboard import (
    build_recording_ops_dashboard_context,
    resolve_article_status,
)
from tests.factories import (
    make_community,
    make_community_member,
    make_discord_linked_user,
    make_event,
    make_event_detail,
)

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "api_v1" / "tests" / "fixtures" / "recording_ops_snapshot_v1.json"


def _load_fixture_payload() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


class RecordingOpsDashboardViewsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = make_discord_linked_user(
            user_name="dash_staff", email="dash_staff@example.com", is_staff=True,
        )
        cls.superuser = make_discord_linked_user(
            user_name="dash_super", email="dash_super@example.com", is_staff=True, is_superuser=True,
        )
        cls.owner = make_discord_linked_user(
            user_name="dash_owner", email="dash_owner@example.com",
        )
        cls.community_staff = make_discord_linked_user(
            user_name="dash_cstaff", email="dash_cstaff@example.com",
        )
        cls.regular_user = make_discord_linked_user(
            user_name="dash_regular", email="dash_regular@example.com",
        )

        cls.community = make_community(name="運用ダッシュボード検証集会", owner=cls.owner, recording_allowed=True)
        make_community_member(cls.community, cls.community_staff, CommunityMember.Role.STAFF)
        cls.disallowed_community = make_community(name="撮影OFF集会", owner=cls.owner, recording_allowed=False)

        cls.event = make_event(cls.community)
        cls.disallowed_event = make_event(cls.disallowed_community)

        cls.published_public = make_event_detail(
            cls.event, status="approved", speaker="登壇者Pub", theme="公開記事あり発表",
            recording_policy=RecordingPolicy.PUBLIC,
            h1="公開記事タイトル", contents="公開済みの記事本文です。",
        )
        cls.waiting_public = make_event_detail(
            cls.event, status="approved", speaker="登壇者Wait", theme="生成待ち記事発表",
            recording_policy=RecordingPolicy.PUBLIC,
            article_consent=EventDetail.ArticleConsent.OK,
            article_generation_requested_at=timezone.now(),
        )
        cls.allowed_uncreated = make_event_detail(
            cls.event, status="approved", speaker="登壇者Allowed", theme="旧設定で記事未作成",
            recording_policy=RecordingPolicy.ALLOWED,
        )
        cls.forbidden_ng = make_event_detail(
            cls.event, status="approved", speaker="登壇者Forbidden", theme="撮影禁止かつ記事NG",
            recording_policy=RecordingPolicy.FORBIDDEN,
            article_consent=EventDetail.ArticleConsent.NG,
        )
        cls.disallowed_public = make_event_detail(
            cls.disallowed_event, status="approved", speaker="登壇者Off", theme="集会許可OFF発表",
            recording_policy=RecordingPolicy.PUBLIC,
        )

    def setUp(self):
        cache.clear()

    def _dashboard_url(self) -> str:
        return reverse("event:recording_ops_dashboard")

    def _list_url(self) -> str:
        return reverse("event:recording_review_list")

    def _create_snapshot(self, *, payload: dict | None = None, snapshot_at=None) -> RecordingOpsSnapshot:
        data = payload if payload is not None else _load_fixture_payload()
        ts = snapshot_at or (timezone.now() - timedelta(hours=1))
        return RecordingOpsSnapshot.objects.create(
            schema_version=1,
            snapshot_at=ts,
            payload=data,
        )

    def test_staff_required_mixin_blocks_anonymous_and_non_staff(self):
        self.client.logout()
        resp = self.client.get(self._dashboard_url())
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].startswith("/account/login/"))

        for non_staff in (self.regular_user, self.owner, self.community_staff):
            self.client.force_login(non_staff)
            with self.subTest(user=non_staff.user_name):
                denied = self.client.get(self._dashboard_url())
                self.assertEqual(denied.status_code, 403)

        self.client.force_login(self.staff)
        ok = self.client.get(self._dashboard_url())
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.headers["Cache-Control"], "private, no-store")
        self.assertEqual(ok.headers["Referrer-Policy"], "same-origin")

    def test_empty_snapshot_renders_empty_state_and_subnav(self):
        self.client.force_login(self.staff)
        resp = self.client.get(self._dashboard_url())
        self.assertEqual(resp.status_code, 200)
        soup = BeautifulSoup(resp.content, "html.parser")
        self.assertIsNotNone(soup.select_one('[data-testid="snapshot-empty-state"]'))
        self.assertContains(resp, "録画運用の統計スナップショットはまだ登録されていません。")
        self.assertIsNotNone(soup.select_one('[data-testid="recording-tab-list"]'))
        self.assertIsNotNone(soup.select_one('[data-testid="recording-tab-dashboard"]'))

    def test_stale_warning_displays_when_snapshot_is_old(self):
        self._create_snapshot(snapshot_at=timezone.now() - timedelta(hours=26))
        self.client.force_login(self.staff)
        resp = self.client.get(self._dashboard_url())
        self.assertEqual(resp.status_code, 200)
        soup = BeautifulSoup(resp.content, "html.parser")
        stale_banner = soup.select_one('[data-testid="snapshot-stale-warning"]')
        self.assertIsNotNone(stale_banner)
        self.assertIn("同期遅延 (stale)", stale_banner.get_text())

        # 新しいスナップショットが来たら stale 警告が消える
        self._create_snapshot(snapshot_at=timezone.now() - timedelta(minutes=15))
        fresh_resp = self.client.get(self._dashboard_url())
        fresh_soup = BeautifulSoup(fresh_resp.content, "html.parser")
        self.assertIsNone(fresh_soup.select_one('[data-testid="snapshot-stale-warning"]'))
        self.assertIn("同期正常", fresh_soup.select_one('[data-testid="snapshot-meta-bar"]').get_text())

    def test_dashboard_renders_all_sections_and_period_switching(self):
        payload = _load_fixture_payload()
        payload["action_items"][0]["detail_id"] = self.published_public.pk
        payload["upcoming_schedule"][0]["talks"] = [
            {
                "detail_id": self.waiting_public.pk,
                "speaker": self.waiting_public.speaker,
                "theme": self.waiting_public.theme,
                "recording_policy": "public",
            },
            {
                "detail_id": self.allowed_uncreated.pk,
                "speaker": self.allowed_uncreated.speaker,
                "theme": self.allowed_uncreated.theme,
                "recording_policy": "allowed",
            },
            {
                "detail_id": self.forbidden_ng.pk,
                "speaker": self.forbidden_ng.speaker,
                "theme": self.forbidden_ng.theme,
                "recording_policy": "forbidden",
            },
        ]
        self._create_snapshot(payload=payload)

        self.client.force_login(self.staff)
        for period, expected_required in (("7", "5"), ("30", "20"), ("90", "50")):
            resp = self.client.get(self._dashboard_url(), {"period": period})
            self.assertEqual(resp.status_code, 200)
            soup = BeautifulSoup(resp.content, "html.parser")
            self.assertIsNotNone(soup.select_one('[data-testid="dashboard-next-session"]'))
            self.assertIn("サンプル技術集会", soup.select_one('[data-testid="dashboard-next-session"]').get_text())
            self.assertIn("要対応 1", soup.select_one('[data-testid="dashboard-action-required"]').get_text())
            funnel = soup.select_one('[data-testid="funnel-metrics"]')
            self.assertEqual(funnel["data-active-period"], period)
            self.assertIn(f"{expected_required}件", funnel.get_text())
            venue = soup.select_one('[data-testid="dashboard-venue-coverage"]')
            self.assertIn("28", venue.get_text())
            self.assertIn("24", venue.get_text())
            self.assertIn("非公開ワールド", venue.get_text())

        # 不正な period クエリは既定 (30日) へフォールバックする
        fallback_resp = self.client.get(self._dashboard_url(), {"period": "invalid"})
        fallback_soup = BeautifulSoup(fallback_resp.content, "html.parser")
        self.assertEqual(fallback_soup.select_one('[data-testid="funnel-metrics"]')["data-active-period"], "30")

    def test_missing_keys_and_unsafe_urls_do_not_crash_or_leak(self):
        malformed_payload = {
            "schema_version": 1,
            "next_session": {"event_url": "https://[broken", "talk_count": "²"},
            "action_items": [
                "unexpected-string-entry",
                {
                    "title": "危険URLテスト",
                    "action_url": "javascript:alert(2)",
                    "detail_id": "²",
                },
                {
                    "title": "巨大桁・不正URLテスト",
                    "action_url": "https://[broken",
                    "detail_id": "5" * 5000,
                },
            ],
            "funnel_stats": {
                "periods": {
                    "7": {"required": 1, "recorded": 10**400, "published": 0, "publishable": 0, "articles": 0},
                    "30": {"required": "bad"},
                }
            },
            "venue_coverage": {},
            "upcoming_schedule": [
                {"community_name": "最小スケジュール", "talks": ["bad-talk", {}]},
            ],
        }
        self._create_snapshot(payload=malformed_payload)
        self.client.force_login(self.staff)
        resp = self.client.get(self._dashboard_url(), {"period": "7"})
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "javascript:alert")
        self.assertContains(resp, "危険URLテスト")
        self.assertContains(resp, "巨大桁・不正URLテスト")
        self.assertContains(resp, "最小スケジュール")
        ctx = build_recording_ops_dashboard_context(user=self.staff, period="7")
        self.assertEqual(ctx.active_funnel.recorded_pct, 100)
        self.assertIsNone(ctx.active_funnel.published_pct)

    def test_article_status_integration_and_consent_boundary_on_list_and_dashboard(self):
        # allowed / forbidden にも RecordingReviewItem があっても再生・録画編集導線を出さない
        for detail in (self.published_public, self.waiting_public, self.allowed_uncreated, self.forbidden_ng):
            RecordingReviewItem.objects.create(
                event_detail=detail,
                proxy_r2_key=f"recording-proxy/{detail.pk}/sample.mp4",
                proxy_expires_at=timezone.now() + timedelta(days=7),
                review_status=RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
            )

        payload = _load_fixture_payload()
        # action_items に allowed_uncreated への危険な URL（難読化・ID 欠損・未解決 ID）が紛れても境界で抑止されること
        payload["action_items"] = [
            {
                "day": "2026-11-24",
                "community_id": self.community.pk,
                "community_name": self.community.name,
                "detail_id": self.allowed_uncreated.pk,
                "title": self.allowed_uncreated.theme,
                "label": "旧設定の境界テスト",
                "reason": "",
                "action_url": f"/event/manage/recordings/00{self.allowed_uncreated.pk}/",
            },
            {
                "day": "2026-11-24",
                "community_id": self.community.pk,
                "community_name": self.community.name,
                "title": "ID欠損トラバーサル境界テスト",
                "label": "ID未指定でもURLから判定",
                "reason": "",
                "action_url": f"/event/manage/recordings/999/../{self.forbidden_ng.pk}/",
            },
            {
                "day": "2026-11-24",
                "community_id": self.community.pk,
                "community_name": self.community.name,
                "title": "未解決ID境界テスト",
                "label": "DB未存在IDは抑止",
                "reason": "",
                "action_url": "/event/manage/recordings/9999999/",
            },
            {
                "day": "2026-11-24",
                "community_id": self.community.pk,
                "community_name": self.community.name,
                "detail_id": self.published_public.pk,
                "title": self.published_public.theme,
                "label": "公開撮影のテスト",
                "reason": "",
                "action_url": f"/event/manage/recordings/{self.published_public.pk}/",
            },
        ]
        payload["upcoming_schedule"] = [
            {
                "day": "2026-11-25",
                "start_time": "22:00",
                "community_id": self.community.pk,
                "community_name": self.community.name,
                "automatic": True,
                "venue_status": "confirmed",
                "talk_count": 4,
                "talks": [
                    {
                        "detail_id": self.published_public.pk,
                        "speaker": self.published_public.speaker,
                        "theme": self.published_public.theme,
                        "recording_policy": "public",
                    },
                    {
                        "detail_id": self.waiting_public.pk,
                        "speaker": self.waiting_public.speaker,
                        "theme": self.waiting_public.theme,
                        "recording_policy": "public",
                    },
                    {
                        "detail_id": self.allowed_uncreated.pk,
                        "speaker": self.allowed_uncreated.speaker,
                        "theme": self.allowed_uncreated.theme,
                        "recording_policy": "allowed",
                    },
                    {
                        "detail_id": self.forbidden_ng.pk,
                        "speaker": self.forbidden_ng.speaker,
                        "theme": self.forbidden_ng.theme,
                        "recording_policy": "forbidden",
                    },
                ],
            }
        ]
        self._create_snapshot(payload=payload)

        # 1) 一般スタッフ（非superuser・非集会メンバー）では「記事」リンクあり・編集リンクなし
        self.client.force_login(self.staff)
        list_resp = self.client.get(self._list_url())
        self.assertEqual(list_resp.status_code, 200)
        list_soup = BeautifulSoup(list_resp.content, "html.parser")
        self.assertIsNotNone(list_soup.select_one('[data-testid="recording-tab-dashboard"]'))

        pub_card = list_soup.select_one(f'[data-testid="recording-review-row"][data-detail-id="{self.published_public.pk}"]')
        self.assertIn("記事: 公開済み", pub_card.get_text())
        self.assertIsNotNone(pub_card.select_one('[data-testid="recording-detail-link"]'))
        self.assertIsNotNone(pub_card.select_one('[data-testid="recording-article-link"]'))
        self.assertIsNone(pub_card.select_one('[data-testid="recording-article-edit-link"]'))

        wait_card = list_soup.select_one(f'[data-testid="recording-review-row"][data-detail-id="{self.waiting_public.pk}"]')
        self.assertIn("記事: 下書き（生成待ち）", wait_card.get_text())

        allowed_card = list_soup.select_one(f'[data-testid="recording-review-row"][data-detail-id="{self.allowed_uncreated.pk}"]')
        self.assertIn("記事: 未作成", allowed_card.get_text())
        self.assertIn("非公開固定（旧設定）", allowed_card.get_text())
        self.assertIsNone(allowed_card.select_one('[data-testid="recording-detail-link"]'))
        self.assertIsNotNone(allowed_card.select_one('[data-testid="recording-article-link"]'))

        forbidden_card = list_soup.select_one(f'[data-testid="recording-review-row"][data-detail-id="{self.forbidden_ng.pk}"]')
        self.assertIn("記事: 記事化 NG", forbidden_card.get_text())
        self.assertIn("非配信", forbidden_card.get_text())
        self.assertIsNone(forbidden_card.select_one('[data-testid="recording-detail-link"]'))
        self.assertIsNotNone(forbidden_card.select_one('[data-testid="recording-article-link"]'))

        # ダッシュボード側でも同じ記事ステータスと同意境界が保たれること
        dash_resp = self.client.get(self._dashboard_url())
        self.assertEqual(dash_resp.status_code, 200)
        dash_soup = BeautifulSoup(dash_resp.content, "html.parser")

        allowed_action = dash_soup.select_one(f'[data-testid="dashboard-action-row"][data-detail-id="{self.allowed_uncreated.pk}"]')
        self.assertIsNotNone(allowed_action)
        self.assertIsNone(allowed_action.select_one('a[href*="/manage/recordings/"]'))
        self.assertIsNotNone(allowed_action.select_one('[data-testid="dashboard-article-link"]'))
        self.assertIsNone(dash_soup.select_one('a[href*="999/../"]'))
        self.assertIsNone(dash_soup.select_one('a[href*="/manage/recordings/9999999/"]'))

        pub_talk = dash_soup.select_one(f'[data-testid="dashboard-schedule-talk"][data-detail-id="{self.published_public.pk}"]')
        self.assertIn("記事: 公開済み", pub_talk.get_text())
        self.assertIsNotNone(pub_talk.select_one('[data-testid="recording-detail-link"]'))
        self.assertIsNone(pub_talk.select_one('[data-testid="dashboard-article-edit-link"]'))

        allowed_talk = dash_soup.select_one(f'[data-testid="dashboard-schedule-talk"][data-detail-id="{self.allowed_uncreated.pk}"]')
        self.assertIn("記事: 未作成", allowed_talk.get_text())
        self.assertIsNone(allowed_talk.select_one('[data-testid="recording-detail-link"]'))

        forbidden_talk = dash_soup.select_one(f'[data-testid="dashboard-schedule-talk"][data-detail-id="{self.forbidden_ng.pk}"]')
        self.assertIn("記事: 記事化 NG", forbidden_talk.get_text())
        self.assertIsNone(forbidden_talk.select_one('[data-testid="recording-detail-link"]'))

        # 2) superuser なら記事編集画面リンクが表示されること
        self.client.force_login(self.superuser)
        super_list = BeautifulSoup(self.client.get(self._list_url()).content, "html.parser")
        self.assertIsNotNone(
            super_list.select_one(
                f'[data-testid="recording-review-row"][data-detail-id="{self.published_public.pk}"] [data-testid="recording-article-edit-link"]'
            )
        )
        super_dash = BeautifulSoup(self.client.get(self._dashboard_url()).content, "html.parser")
        self.assertIsNotNone(
            super_dash.select_one(
                f'[data-testid="dashboard-schedule-talk"][data-detail-id="{self.published_public.pk}"] [data-testid="dashboard-article-edit-link"]'
            )
        )

    def test_resolve_article_status_handles_unpublished_draft_and_service_context(self):
        unpublished_draft = make_event_detail(
            self.event, status="pending", speaker="登壇者Draft", theme="承認前下書き",
            h1="下書きタイトル", contents="承認前の記事本文",
        )
        summary = resolve_article_status(unpublished_draft, user=self.superuser)
        self.assertEqual(summary.code, "draft")
        self.assertEqual(summary.label, "記事: 下書き（未公開）")
        self.assertIsNotNone(summary.edit_url)

        ctx = build_recording_ops_dashboard_context(user=self.staff, period="30")
        self.assertFalse(ctx.has_snapshot)
