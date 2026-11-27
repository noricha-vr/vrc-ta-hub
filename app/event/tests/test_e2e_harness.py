"""E2E 検証スタック（分離検証・署名ホスト分離・KPI 検証逆テスト）のテスト。"""
from __future__ import annotations

import io
import json
import os
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from community.constants import RecordingPolicy
from event.management.commands.e2e_check_isolation import validate_e2e_isolation
from event.management.commands.e2e_verify_kpi import evaluate_e2e_kpi
from event.models import EventDetail, RecordingReviewItem, article_body_hash
from event.services.recording_ops.storage import (
    RecordingStorageConfigError,
    create_presigned_proxy_download_url,
    create_presigned_proxy_upload_url,
)
from event.services.youtube_service import get_transcript
from tests.factories import make_community, make_event, make_event_detail

E2E_SAFE_ENV = {
    "GOOGLE_API_KEY": "e2e-dummy-g",
    "GOOGLE_CALENDAR_ID": "e2e-dummy-cal",
    "GEMINI_API_KEY": "e2e-dummy-gem",
    "OPENAI_API_KEY": "e2e-dummy-oa",
    "X_API_KEY": "e2e-dummy-x",
    "X_API_SECRET": "e2e-dummy-xs",
    "X_ACCESS_TOKEN": "e2e-dummy-xt",
    "X_ACCESS_TOKEN_SECRET": "e2e-dummy-xts",
    "DISCORD_BOT_TOKEN": "e2e-dummy-bot",
}


class E2EStorageAndIsolationTest(TestCase):
    def setUp(self):
        self.community = make_community(name="E2E テスト集会", recording_allowed=True)
        self.event = make_event(self.community)
        self.detail = make_event_detail(
            self.event,
            status="approved",
            recording_policy=RecordingPolicy.PUBLIC,
        )

    @override_settings(
        DEBUG=True,
        E2E_LOOPBACK_STORAGE=True,
        E2E_DISABLE_EXTERNAL_YOUTUBE=True,
        AWS_ACCESS_KEY_ID="minioadmin",
        AWS_SECRET_ACCESS_KEY="minioadmin",
        AWS_STORAGE_BUCKET_NAME="vrc-ta-hub-e2e-public",
        AWS_PRIVATE_STORAGE_BUCKET_NAME="vrc-ta-hub-e2e-private",
        AWS_S3_ENDPOINT_URL="http://storage:9000",
        AWS_S3_PRESIGN_ENDPOINT_URL="http://127.0.0.1:19000",
        DISCORD_WEBHOOK_URL="http://host.docker.internal:18089/webhook",
        DISCORD_REPORT_WEBHOOK_URL="http://host.docker.internal:18089/report",
        DISCORD_ANNOUNCE_WEBHOOK_URL="http://host.docker.internal:18089/announce",
        CSRF_TRUSTED_ORIGIN="http://127.0.0.1:18015",
    )
    def test_e2e_isolation_passes_on_allowlisted_config(self):
        with patch.dict(os.environ, E2E_SAFE_ENV, clear=False):
            result = validate_e2e_isolation()
        self.assertTrue(result["ok"], result["violations"])

    @override_settings(
        DEBUG=True,
        E2E_LOOPBACK_STORAGE=True,
        E2E_DISABLE_EXTERNAL_YOUTUBE=True,
        AWS_STORAGE_BUCKET_NAME="vrc-ta-hub-e2e-public",
        AWS_PRIVATE_STORAGE_BUCKET_NAME="vrc-ta-hub-e2e-private",
        AWS_S3_ENDPOINT_URL="https://example.r2.cloudflarestorage.com",
        DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/123/abc",
    )
    def test_e2e_isolation_rejects_production_urls(self):
        with patch.dict(os.environ, E2E_SAFE_ENV, clear=False):
            result = validate_e2e_isolation()
        self.assertFalse(result["ok"])
        self.assertTrue(any("r2.cloudflarestorage.com" in v for v in result["violations"]))
        self.assertTrue(any("discord.com" in v for v in result["violations"]))

    @override_settings(
        DEBUG=False,
        E2E_LOOPBACK_STORAGE=True,
        AWS_ACCESS_KEY_ID="minioadmin",
        AWS_SECRET_ACCESS_KEY="minioadmin",
        AWS_STORAGE_BUCKET_NAME="vrc-ta-hub-e2e-public",
        AWS_PRIVATE_STORAGE_BUCKET_NAME="vrc-ta-hub-e2e-private",
        AWS_S3_ENDPOINT_URL="http://storage:9000",
        AWS_S3_PRESIGN_ENDPOINT_URL="http://127.0.0.1:19000",
    )
    def test_loopback_storage_refused_when_debug_false(self):
        with self.assertRaises(RecordingStorageConfigError):
            create_presigned_proxy_upload_url(self.detail)

    @override_settings(
        DEBUG=True,
        E2E_LOOPBACK_STORAGE=True,
        AWS_ACCESS_KEY_ID="minioadmin",
        AWS_SECRET_ACCESS_KEY="minioadmin",
        AWS_STORAGE_BUCKET_NAME="vrc-ta-hub-e2e-public",
        AWS_PRIVATE_STORAGE_BUCKET_NAME="vrc-ta-hub-e2e-private",
        AWS_S3_ENDPOINT_URL="http://storage:9000",
        AWS_S3_PRESIGN_ENDPOINT_URL="http://127.0.0.1:19000",
    )
    def test_presigned_upload_and_download_use_presign_endpoint_host(self):
        upload = create_presigned_proxy_upload_url(self.detail)
        self.assertTrue(upload.upload_url.startswith("http://127.0.0.1:19000/vrc-ta-hub-e2e-private/"))

        RecordingReviewItem.objects.create(
            event_detail=self.detail,
            review_status=RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED,
            youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.UNLISTED,
            proxy_r2_key=upload.object_key,
            proxy_uploaded_at=timezone.now(),
            proxy_expires_at=timezone.now() + timedelta(days=7),
        )
        download = create_presigned_proxy_download_url(self.detail)
        self.assertTrue(download.proxy_url.startswith("http://127.0.0.1:19000/vrc-ta-hub-e2e-private/"))

    @override_settings(DEBUG=True, E2E_DISABLE_EXTERNAL_YOUTUBE=True)
    def test_external_youtube_disabled_in_e2e_mode(self):
        with patch("event.services.youtube_service.build") as mock_build:
            self.assertIsNone(get_transcript("dQw4w9WgXcQ"))
            mock_build.assert_not_called()

    @override_settings(
        DEBUG=True,
        E2E_LOOPBACK_STORAGE=True,
        E2E_DISABLE_EXTERNAL_YOUTUBE=True,
        DATABASES={"default": {"ENGINE": "django.db.backends.mysql", "HOST": "localhost", "NAME": "vrc_ta_hub", "USER": "root"}},
        AWS_STORAGE_BUCKET_NAME="vrc-ta-hub-e2e-public",
        AWS_PRIVATE_STORAGE_BUCKET_NAME="vrc-ta-hub-e2e-private",
        AWS_S3_ENDPOINT_URL="http://storage:9000",
        AWS_S3_PRESIGN_ENDPOINT_URL="http://127.0.0.1:19000",
        SITE_URL="http://127.0.0.1:18015",
    )
    def test_e2e_isolation_rejects_non_e2e_database(self):
        with patch.dict(os.environ, E2E_SAFE_ENV, clear=False):
            result = validate_e2e_isolation()
        self.assertFalse(result["ok"])
        self.assertTrue(any("NAME='vrc_ta_hub'" in v for v in result["violations"]))
        self.assertTrue(any("USER='root'" in v for v in result["violations"]))

    @override_settings(DEBUG=True, E2E_LOOPBACK_STORAGE=True)
    def test_e2e_blocks_external_discord_webhook_delivery(self):
        from website.discord_webhook import post_discord_webhook

        with patch("website.discord_webhook.requests.post") as mock_post:
            with self.assertRaises(ValueError):
                post_discord_webhook("https://discord.com/api/webhooks/123/secret", {"content": "x"})
            mock_post.assert_not_called()

    @override_settings(
        DEBUG=True,
        E2E_LOOPBACK_STORAGE=True,
        E2E_DISABLE_EXTERNAL_YOUTUBE=True,
        AWS_STORAGE_BUCKET_NAME="vrc-ta-hub-e2e-public",
        AWS_PRIVATE_STORAGE_BUCKET_NAME="vrc-ta-hub-e2e-private",
        AWS_S3_ENDPOINT_URL="http://storage:9000",
        AWS_S3_PRESIGN_ENDPOINT_URL="http://127.0.0.1:19000",
        SITE_URL="http://127.0.0.1:18015",
    )
    def test_seed_rejects_non_e2e_community_name(self):
        with patch.dict(os.environ, E2E_SAFE_ENV, clear=False):
            with self.assertRaises(SystemExit) as ctx:
                call_command("e2e_seed_recording", "--community-name", "本番集会", "--reset")
            self.assertEqual(ctx.exception.code, 2)


class E2EVerifyKpiTest(TestCase):
    def _make_passing_fixture(self) -> tuple[EventDetail, RecordingReviewItem, str]:
        vid = "E2EVideo001"
        community = make_community(name="KPI 検証集会", recording_allowed=True)
        event = make_event(community, event_date=timezone.localdate())
        h1 = "E2E テスト記事タイトル"
        contents = "E2E テスト記事の本文です。"
        meta = "E2E テスト要約"
        detail = make_event_detail(
            event,
            detail_type="LT",
            status="approved",
            recording_policy=RecordingPolicy.PUBLIC,
            article_consent=EventDetail.ArticleConsent.OK,
            youtube_url=f"https://youtu.be/{vid}",
            h1=h1,
            contents=contents,
            meta_description=meta,
            article_body_hash=article_body_hash(h1, contents, meta),
            article_source_video_id=vid,
        )
        item = RecordingReviewItem.objects.create(
            event_detail=detail,
            review_status=RecordingReviewItem.ReviewStatus.PUBLISHED,
            youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.PUBLIC,
            youtube_draft_video_id=vid,
        )
        return detail, item, vid

    def test_verify_kpi_passes_on_healthy_fixture(self):
        detail, _, vid = self._make_passing_fixture()
        result = evaluate_e2e_kpi(detail_id=detail.pk, expected_video_id=vid)
        self.assertTrue(result["ok"], result)

    def test_verify_kpi_fails_when_each_kpi_is_broken(self):
        # 1. review_status != published
        detail, item, vid = self._make_passing_fixture()
        item.review_status = RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED
        item.save(update_fields=["review_status"])
        res = evaluate_e2e_kpi(detail_id=detail.pk, expected_video_id=vid)
        self.assertFalse(res["ok"])
        self.assertIn("review_status_published", res["failures"])

        # 2. youtube_url mismatch
        detail, _, vid = self._make_passing_fixture()
        detail.youtube_url = "https://youtu.be/OtherVid001"
        detail.save(update_fields=["youtube_url"])
        res = evaluate_e2e_kpi(detail_id=detail.pk, expected_video_id=vid)
        self.assertFalse(res["ok"])
        self.assertIn("youtube_url_matches_video_id", res["failures"])

        # 3. article_source_video_id mismatch (PDF-only / stale)
        detail, _, vid = self._make_passing_fixture()
        detail.article_source_video_id = ""
        detail.save(update_fields=["article_source_video_id"])
        res = evaluate_e2e_kpi(detail_id=detail.pk, expected_video_id=vid)
        self.assertFalse(res["ok"])
        self.assertIn("article_source_video_id_matches", res["failures"])
        self.assertIn("video_article_ready", res["failures"])

        # 4. article_state != AUTO (empty article)
        detail, _, vid = self._make_passing_fixture()
        detail.h1 = ""
        detail.contents = ""
        detail.save(update_fields=["h1", "contents"])
        res = evaluate_e2e_kpi(detail_id=detail.pk, expected_video_id=vid)
        self.assertFalse(res["ok"])
        self.assertIn("article_state_auto", res["failures"])

    @override_settings(
        DEBUG=True,
        E2E_LOOPBACK_STORAGE=True,
        E2E_DISABLE_EXTERNAL_YOUTUBE=True,
        AWS_STORAGE_BUCKET_NAME="vrc-ta-hub-e2e-public",
        AWS_PRIVATE_STORAGE_BUCKET_NAME="vrc-ta-hub-e2e-private",
        AWS_S3_ENDPOINT_URL="http://storage:9000",
        AWS_S3_PRESIGN_ENDPOINT_URL="http://127.0.0.1:19000",
        DISCORD_WEBHOOK_URL="http://host.docker.internal:18089/webhook",
        DISCORD_REPORT_WEBHOOK_URL="http://host.docker.internal:18089/report",
        DISCORD_ANNOUNCE_WEBHOOK_URL="http://host.docker.internal:18089/announce",
        CSRF_TRUSTED_ORIGIN="http://127.0.0.1:18015",
    )
    def test_seed_command_outputs_json_ids(self):
        out = io.StringIO()
        with patch.dict(os.environ, E2E_SAFE_ENV, clear=False):
            call_command("e2e_seed_recording", "--reset", stdout=out)
        data = json.loads(out.getvalue())
        self.assertIn("event_detail_id", data)
        self.assertTrue(EventDetail.objects.filter(pk=data["event_detail_id"]).exists())
