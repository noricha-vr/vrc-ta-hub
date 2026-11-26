"""録画運用向け 記事生成ステータス一括取得 API のテスト。"""
from __future__ import annotations

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from community.models import Community
from event.models import Event, EventDetail
from user_account.models import APIKey

User = get_user_model()


class RecordingOpsArticleStatusAPITest(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            user_name="super_ops", email="super_ops@example.com", password="pw"
        )
        self.staff = User.objects.create_user(
            user_name="staff_ops", email="staff_ops@example.com", password="pw", is_staff=True
        )
        self.normal = User.objects.create_user(
            user_name="normal_ops", email="normal_ops@example.com", password="pw"
        )
        _, self.super_key = APIKey.create_with_raw_key(user=self.superuser, name="super")
        _, self.staff_key = APIKey.create_with_raw_key(user=self.staff, name="staff")
        _, self.normal_key = APIKey.create_with_raw_key(user=self.normal, name="normal")

        self.community = Community.objects.create(name="テスト集会", status="approved")
        self.event = Event.objects.create(
            community=self.community, date=date(2026, 11, 26), start_time="22:00:00"
        )
        self.url = reverse("recording-ops-article-status")

    def _client(self, raw_key: str | None = None) -> APIClient:
        client = APIClient()
        if raw_key:
            client.credentials(HTTP_X_API_KEY=raw_key)
        return client

    def _make_detail(self, **kwargs) -> EventDetail:
        defaults = dict(
            event=self.event,
            detail_type="LT",
            status="approved",
            speaker="登壇者",
            theme="テーマ",
            article_consent=EventDetail.ArticleConsent.OK,
        )
        defaults.update(kwargs)
        return EventDetail.objects.create(**defaults)

    def test_permissions(self):
        detail = self._make_detail()
        resp = self._client().get(f"{self.url}?ids={detail.pk}")
        self.assertIn(resp.status_code, (401, 403))
        for key in (self.normal_key, self.staff_key):
            resp = self._client(key).get(f"{self.url}?ids={detail.pk}")
            self.assertEqual(resp.status_code, 403)

    def test_all_seven_states(self):
        now = timezone.now()
        # 1. generated_current_video
        d_gen = self._make_detail(
            youtube_url="https://youtu.be/dQw4w9WgXcQ",
            h1="自動記事",
            contents="本文",
            article_source_video_id="dQw4w9WgXcQ",
            article_generated_at=now,
        )
        d_gen.article_body_hash = d_gen.current_article_hash()
        d_gen.save(update_fields=["article_body_hash"])

        # 2. generated_pdf_only (article exists from PDF, video added later but not queued/failed)
        d_pdf = self._make_detail(
            youtube_url="https://youtu.be/abc12345678",
            h1="PDF記事",
            contents="本文",
            article_source_video_id="",
            article_generated_at=now,
        )
        d_pdf.article_body_hash = d_pdf.current_article_hash()
        d_pdf.save(update_fields=["article_body_hash"])

        # 3. manual (no matching body hash -> manually authored/edited)
        d_manual = self._make_detail(
            youtube_url="https://youtu.be/xyz98765432",
            h1="手動記事",
            contents="手で書いた本文",
            article_body_hash="",
        )

        # 4. queued
        d_queued = self._make_detail(youtube_url="https://youtu.be/que12345678")
        EventDetail.objects.filter(pk=d_queued.pk).update(
            article_generation_requested_at=now + timedelta(minutes=10),
            article_generation_attempts=1,
            article_generation_deferrals=2,
        )

        # 5. failed (requested_at is None, last_error non-empty, no article)
        d_failed = self._make_detail(youtube_url="https://youtu.be/fai12345678")
        EventDetail.objects.filter(pk=d_failed.pk).update(
            article_generation_requested_at=None,
            article_generation_attempts=5,
            article_generation_last_error="transcript_unavailable",
        )

        # 6. not_queued (consent OK, no article, not requested, no error)
        d_not_queued = self._make_detail(youtube_url="https://youtu.be/noq12345678")
        EventDetail.objects.filter(pk=d_not_queued.pk).update(
            article_generation_requested_at=None,
            article_generation_last_error="",
        )

        # 7. not_applicable (consent NG)
        d_ng = self._make_detail(
            youtube_url="https://youtu.be/ng012345678",
            article_consent=EventDetail.ArticleConsent.NG,
        )

        # 8. soft-deleted -> excluded
        d_del = self._make_detail(deleted_at=now)

        ids = ",".join(str(d.pk) for d in (d_gen, d_pdf, d_manual, d_queued, d_failed, d_not_queued, d_ng, d_del))
        resp = self._client(self.super_key).get(f"{self.url}?ids={ids}")
        self.assertEqual(resp.status_code, 200)
        items = resp.json()["items"]
        self.assertEqual(items[str(d_gen.pk)]["state"], "generated_current_video")
        self.assertEqual(items[str(d_pdf.pk)]["state"], "generated_pdf_only")
        self.assertEqual(items[str(d_manual.pk)]["state"], "manual")
        self.assertEqual(items[str(d_queued.pk)]["state"], "queued")
        self.assertEqual(items[str(d_queued.pk)]["attempts"], 1)
        self.assertEqual(items[str(d_queued.pk)]["deferrals"], 2)
        self.assertEqual(items[str(d_failed.pk)]["state"], "failed")
        self.assertEqual(items[str(d_failed.pk)]["last_error_code"], "transcript_unavailable")
        self.assertEqual(items[str(d_not_queued.pk)]["state"], "not_queued")
        self.assertEqual(items[str(d_ng.pk)]["state"], "not_applicable")
        self.assertNotIn(str(d_del.pk), items)

    def test_validation_errors(self):
        client = self._client(self.super_key)
        for query in ("", "?ids=", "?ids=abc", "?ids=0", f"?ids={','.join(str(i) for i in range(1, 102))}"):
            resp = client.get(f"{self.url}{query}")
            self.assertEqual(resp.status_code, 400, msg=query)

    def test_read_scope_superuser_key_allowed(self):
        detail = self._make_detail()
        read_obj, read_key = APIKey.create_with_raw_key(user=self.superuser, name="super-read")
        read_obj.scope = APIKey.SCOPE_READ
        read_obj.save(update_fields=["scope"])
        resp = self._client(read_key).get(f"{self.url}?ids={detail.pk}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(str(detail.pk), resp.json()["items"])

    def test_openapi_schema_includes_endpoint(self):
        resp = APIClient().get(reverse("schema"), HTTP_ACCEPT="application/vnd.oai.openapi+json")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("/api/v1/recording-ops/article-status/", resp.json()["paths"])
