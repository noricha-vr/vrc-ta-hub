"""非公開 R2 バケット署名 URL 発給サービスの単体テスト。"""
from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from community.constants import RecordingPolicy
from event.models import RecordingReviewItem
from event.services.recording_ops.storage import (
    DEFAULT_PRESIGNED_EXPIRES_IN,
    MAX_PRESIGNED_EXPIRES_IN,
    MIN_PRESIGNED_EXPIRES_IN,
    RecordingConsentError,
    RecordingObjectKeyError,
    RecordingProxyExpiredError,
    RecordingProxyNotFoundError,
    RecordingStorageConfigError,
    clamp_presigned_expires_in,
    create_presigned_proxy_download_url,
    create_presigned_proxy_upload_url,
    validate_proxy_object_key,
    validate_transcript_object_key,
)
from tests.factories import make_community, make_event, make_event_detail


def _storage_settings(**overrides):
    base = {
        "AWS_ACCESS_KEY_ID": "test-r2-access-key",
        "AWS_SECRET_ACCESS_KEY": "super-secret-r2-key-value",
        "AWS_STORAGE_BUCKET_NAME": "vrc-ta-hub-public",
        "AWS_PRIVATE_STORAGE_BUCKET_NAME": "vrc-ta-hub-private",
        "AWS_S3_ENDPOINT_URL": "https://r2.example.invalid",
    }
    base.update(overrides)
    return override_settings(**base)


class RecordingStorageKeyAllowlistTest(TestCase):
    def test_validate_proxy_object_key_accepts_hex_and_uuid4_and_rejects_traversal(self):
        uid_hex = uuid.uuid4().hex
        uid_dashed = str(uuid.uuid4())
        self.assertEqual(
            validate_proxy_object_key(42, f"recording-proxy/42/{uid_hex}.mp4"),
            f"recording-proxy/42/{uid_hex}.mp4",
        )
        self.assertEqual(
            validate_proxy_object_key(42, f"recording-proxy/42/{uid_dashed}.mp4"),
            f"recording-proxy/42/{uid_dashed}.mp4",
        )

        bad_keys = [
            "",
            f"recording-proxy/99/{uid_hex}.mp4",
            f"recording-proxy/42/../41/{uid_hex}.mp4",
            f"recording-proxy/42/{uid_hex}.mp4\n",
            f"recording-proxy/42/{uid_hex}.mov",
            f"recording-proxy/42/speaker-name-{uid_hex}.mp4",
            f"recording-proxy/42\\{uid_hex}.mp4",
        ]
        for bad in bad_keys:
            with self.subTest(bad=bad), self.assertRaises(RecordingObjectKeyError):
                validate_proxy_object_key(42, bad)

    def test_validate_transcript_object_key_allowlist(self):
        uid_hex = uuid.uuid4().hex
        for ext in ("json", "txt", "srt", "vtt"):
            key = f"recording-transcript/15/{uid_hex}.{ext}"
            self.assertEqual(validate_transcript_object_key(15, key), key)

        for bad in (
            f"recording-transcript/16/{uid_hex}.json",
            f"recording-transcript/15/{uid_hex}.exe",
            f"recording-transcript/15/../{uid_hex}.json",
        ):
            with self.subTest(bad=bad), self.assertRaises(RecordingObjectKeyError):
                validate_transcript_object_key(15, bad)

    def test_clamp_presigned_expires_in_enforces_15_to_30_minutes(self):
        self.assertEqual(clamp_presigned_expires_in(None), DEFAULT_PRESIGNED_EXPIRES_IN)
        self.assertEqual(clamp_presigned_expires_in(1), MIN_PRESIGNED_EXPIRES_IN)
        self.assertEqual(clamp_presigned_expires_in(899), MIN_PRESIGNED_EXPIRES_IN)
        self.assertEqual(clamp_presigned_expires_in(900), 900)
        self.assertEqual(clamp_presigned_expires_in(1500), 1500)
        self.assertEqual(clamp_presigned_expires_in(1800), 1800)
        self.assertEqual(clamp_presigned_expires_in(7200), MAX_PRESIGNED_EXPIRES_IN)
        with self.assertRaises(ValueError):
            clamp_presigned_expires_in(True)  # type: ignore[arg-type]


class RecordingStoragePresignTest(TestCase):
    def setUp(self):
        self.community = make_community(name="公開撮影集会", recording_allowed=True)
        self.event = make_event(self.community)
        self.detail = make_event_detail(
            self.event,
            status="approved",
            recording_policy=RecordingPolicy.PUBLIC,
        )

    @_storage_settings(AWS_STORAGE_BUCKET_NAME="vrc-ta-hub-private", AWS_PRIVATE_STORAGE_BUCKET_NAME="vrc-ta-hub-private")
    def test_rejects_when_private_and_public_buckets_are_identical(self):
        with self.assertRaises(RecordingStorageConfigError):
            create_presigned_proxy_upload_url(self.detail)

    @_storage_settings(AWS_S3_ENDPOINT_URL="http://insecure.example.invalid")
    def test_rejects_non_https_endpoint(self):
        with self.assertRaises(RecordingStorageConfigError):
            create_presigned_proxy_upload_url(self.detail)

    @_storage_settings()
    @patch("event.services.recording_ops.storage.boto3.client")
    def test_upload_url_issues_s3v4_private_bucket_and_does_not_log_secrets(self, mock_boto_client):
        mock_s3 = MagicMock()
        presigned = "https://r2.example.invalid/vrc-ta-hub-private/signed?X-Amz-Signature=secret-sig"
        mock_s3.generate_presigned_url.return_value = presigned
        mock_boto_client.return_value = mock_s3

        with self.assertLogs("event.services.recording_ops.storage", level="INFO") as logs:
            result = create_presigned_proxy_upload_url(
                self.detail,
                expires_in=60,  # 900 にクランプ
                include_transcript=True,
                transcript_ext="vtt",
            )

        self.assertEqual(result.expires_in, MIN_PRESIGNED_EXPIRES_IN)
        self.assertEqual(result.upload_url, presigned)
        self.assertTrue(result.object_key.startswith(f"recording-proxy/{self.detail.pk}/"))
        self.assertTrue(result.object_key.endswith(".mp4"))
        self.assertIsNotNone(result.transcript_object_key)
        self.assertTrue(result.transcript_object_key.startswith(f"recording-transcript/{self.detail.pk}/"))
        self.assertTrue(result.transcript_object_key.endswith(".vtt"))

        client_kwargs = mock_boto_client.call_args.kwargs
        self.assertEqual(client_kwargs["endpoint_url"], "https://r2.example.invalid")
        self.assertEqual(client_kwargs["config"].signature_version, "s3v4")

        first_call = mock_s3.generate_presigned_url.call_args_list[0]
        self.assertEqual(first_call.args[0], "put_object")
        self.assertEqual(first_call.kwargs["Params"]["Bucket"], "vrc-ta-hub-private")
        self.assertEqual(first_call.kwargs["Params"]["Key"], result.object_key)
        self.assertEqual(first_call.kwargs["ExpiresIn"], 900)

        joined_logs = "\n".join(logs.output)
        self.assertNotIn("secret-sig", joined_logs)
        self.assertNotIn("super-secret-r2-key-value", joined_logs)
        self.assertNotIn("test-r2-access-key", joined_logs)

    @_storage_settings()
    def test_consent_boundary_rejects_allowed_forbidden_and_disallowed_community(self):
        for policy in (RecordingPolicy.ALLOWED, RecordingPolicy.FORBIDDEN):
            self.detail.recording_policy = policy
            self.detail.save(update_fields=["recording_policy"])
            with self.subTest(policy=policy), self.assertRaises(RecordingConsentError):
                create_presigned_proxy_upload_url(self.detail)

        self.detail.recording_policy = RecordingPolicy.PUBLIC
        self.detail.save(update_fields=["recording_policy"])
        self.community.recording_allowed = False
        self.community.save(update_fields=["recording_allowed"])
        with self.assertRaises(RecordingConsentError):
            create_presigned_proxy_upload_url(self.detail)

    @_storage_settings()
    @patch("event.services.recording_ops.storage.boto3.client")
    def test_download_url_checks_missing_expired_key_and_clamps_ttl(self, mock_boto_client):
        with self.assertRaises(RecordingProxyNotFoundError):
            create_presigned_proxy_download_url(self.detail)

        key = f"recording-proxy/{self.detail.pk}/{uuid.uuid4().hex}.mp4"
        item = RecordingReviewItem.objects.create(
            event_detail=self.detail,
            proxy_r2_key=key,
            review_status=RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
            proxy_expires_at=timezone.now() + timedelta(days=7),
        )

        mock_s3 = MagicMock()
        presigned = "https://r2.example.invalid/vrc-ta-hub-private/get?X-Amz-Signature=get-secret-sig"
        mock_s3.generate_presigned_url.return_value = presigned
        mock_boto_client.return_value = mock_s3

        with self.assertLogs("event.services.recording_ops.storage", level="INFO") as logs:
            result = create_presigned_proxy_download_url(self.detail, expires_in=9999)

        self.assertEqual(result.expires_in, MAX_PRESIGNED_EXPIRES_IN)
        self.assertEqual(result.proxy_url, presigned)
        self.assertEqual(mock_s3.generate_presigned_url.call_args.args[0], "get_object")
        self.assertEqual(mock_s3.generate_presigned_url.call_args.kwargs["Params"]["Bucket"], "vrc-ta-hub-private")
        self.assertEqual(mock_s3.generate_presigned_url.call_args.kwargs["ExpiresIn"], 1800)
        self.assertNotIn("get-secret-sig", "\n".join(logs.output))

        # 期限まで 20 分残っている場合、1800 秒要求しても残存時間内に収まる
        item.proxy_expires_at = timezone.now() + timedelta(seconds=1205)
        item.save(update_fields=["proxy_expires_at"])
        capped = create_presigned_proxy_download_url(self.detail, expires_in=1800)
        self.assertGreaterEqual(capped.expires_in, MIN_PRESIGNED_EXPIRES_IN)
        self.assertLessEqual(capped.expires_in, 1205)

        # 残り 15 分未満も失効扱いで拒否（有効期限が保持期限を超えない）
        item.proxy_expires_at = timezone.now() + timedelta(seconds=600)
        item.save(update_fields=["proxy_expires_at"])
        with self.assertRaises(RecordingProxyExpiredError):
            create_presigned_proxy_download_url(self.detail)

        # 期限切れ日時超過
        item.proxy_expires_at = timezone.now() - timedelta(seconds=1)
        item.save(update_fields=["proxy_expires_at"])
        with self.assertRaises(RecordingProxyExpiredError):
            create_presigned_proxy_download_url(self.detail)

        # expired 状態
        item.proxy_expires_at = timezone.now() + timedelta(days=7)
        item.review_status = RecordingReviewItem.ReviewStatus.EXPIRED
        item.save(update_fields=["proxy_expires_at", "review_status"])
        with self.assertRaises(RecordingProxyExpiredError):
            create_presigned_proxy_download_url(self.detail)

        # DB 上のキー改ざん（パストラバーサル/他発表キー）を許可リストで遮断
        item.review_status = RecordingReviewItem.ReviewStatus.NEEDS_REVIEW
        item.proxy_r2_key = f"recording-proxy/{self.detail.pk + 1}/{uuid.uuid4().hex}.mp4"
        item.save(update_fields=["review_status", "proxy_r2_key"])
        with self.assertRaises(RecordingObjectKeyError):
            create_presigned_proxy_download_url(self.detail)
