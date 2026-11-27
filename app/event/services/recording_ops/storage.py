"""非公開 R2 バケットへのプロキシ動画署名 URL 発給サービス（Layer 2）。"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import boto3
from botocore.client import Config
from django.conf import settings
from django.utils import timezone

from community.constants import RecordingPolicy
from event.models import EventDetail, RecordingReviewItem

logger = logging.getLogger(__name__)

MIN_PRESIGNED_EXPIRES_IN = 900
MAX_PRESIGNED_EXPIRES_IN = 1800
DEFAULT_PRESIGNED_EXPIRES_IN = 1200

_UUID_SEGMENT_PATTERN = r"(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
_TRANSCRIPT_CONTENT_TYPES: dict[str, str] = {
    "json": "application/json",
    "txt": "text/plain; charset=utf-8",
    "srt": "application/x-subrip",
    "vtt": "text/vtt",
}


class RecordingStorageError(Exception):
    """プロキシ動画ストレージ操作の基底例外。"""

    code = "recording_storage_error"

    def __init__(self, detail: str, *, code: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        if code is not None:
            self.code = code


class RecordingStorageConfigError(RecordingStorageError):
    """非公開バケット分離・R2 設定の不備。"""

    code = "recording_storage_misconfigured"


class RecordingConsentError(RecordingStorageError):
    """同意境界（recording_policy == public かつ community.recording_allowed）を満たさない。"""

    code = "recording_not_permitted"


class RecordingObjectKeyError(RecordingStorageError):
    """オブジェクトキーが許可リストに一致しない。"""

    code = "invalid_object_key"


class RecordingProxyNotFoundError(RecordingStorageError):
    """対象発表のプロキシ動画が未登録。"""

    code = "proxy_not_found"


class RecordingProxyExpiredError(RecordingStorageError):
    """対象発表のプロキシ動画が期限切れ。"""

    code = "proxy_expired"


@dataclass(frozen=True)
class PresignedUploadResult:
    event_detail_id: int
    object_key: str
    upload_url: str
    headers: dict[str, str]
    expires_in: int
    expires_at: datetime
    method: str = "PUT"
    transcript_object_key: str | None = None
    transcript_upload_url: str | None = None
    transcript_headers: dict[str, str] | None = None


@dataclass(frozen=True)
class PresignedDownloadResult:
    review_item: RecordingReviewItem
    proxy_url: str
    expires_in: int
    expires_at: datetime


def _require_positive_detail_id(detail_id: int) -> int:
    if type(detail_id) is not int or detail_id <= 0:
        raise RecordingObjectKeyError("event_detail_id は正の整数である必要があります。")
    return detail_id


def _validate_key_common(object_key: str) -> None:
    if not isinstance(object_key, str) or not object_key:
        raise RecordingObjectKeyError("オブジェクトキーを指定してください。")
    if any(forbidden in object_key for forbidden in ("..", "\\", "\n", "\r", "\0")):
        raise RecordingObjectKeyError("不正なオブジェクトキーです。")


def validate_proxy_object_key(detail_id: int, object_key: str) -> str:
    """プロキシ動画キーが `recording-proxy/<detail_id>/<uuid>.mp4` 許可リストに一致するか検証する。"""
    validated_id = _require_positive_detail_id(detail_id)
    _validate_key_common(object_key)
    pattern = rf"\Arecording-proxy/{validated_id}/{_UUID_SEGMENT_PATTERN}\.mp4\Z"
    if re.fullmatch(pattern, object_key) is None:
        raise RecordingObjectKeyError("proxy_r2_key が許可されたパス形式と一致しません。")
    return object_key


def validate_transcript_object_key(detail_id: int, object_key: str) -> str:
    """文字起こしキーが `recording-transcript/<detail_id>/<uuid>.(json|txt|srt|vtt)` に一致するか検証する。"""
    validated_id = _require_positive_detail_id(detail_id)
    _validate_key_common(object_key)
    pattern = rf"\Arecording-transcript/{validated_id}/{_UUID_SEGMENT_PATTERN}\.(?:json|txt|srt|vtt)\Z"
    if re.fullmatch(pattern, object_key) is None:
        raise RecordingObjectKeyError("transcript_r2_key が許可されたパス形式と一致しません。")
    return object_key


def generate_proxy_object_key(detail_id: int) -> str:
    """元ファイル名や個人名を含まないプロキシ用 R2 オブジェクトキーを生成する。"""
    validated_id = _require_positive_detail_id(detail_id)
    return f"recording-proxy/{validated_id}/{uuid.uuid4().hex}.mp4"


def generate_transcript_object_key(detail_id: int, ext: str = "json") -> str:
    """文字起こし用 R2 オブジェクトキーを生成する。"""
    validated_id = _require_positive_detail_id(detail_id)
    if ext not in _TRANSCRIPT_CONTENT_TYPES:
        raise RecordingObjectKeyError("transcript_ext は json / txt / srt / vtt のいずれかで指定してください。")
    return f"recording-transcript/{validated_id}/{uuid.uuid4().hex}.{ext}"


def clamp_presigned_expires_in(expires_in: int | None) -> int:
    """署名 URL の有効期限を 15〜30 分（900〜1800 秒）にクランプする。"""
    if expires_in is None:
        return DEFAULT_PRESIGNED_EXPIRES_IN
    if type(expires_in) is not int:
        raise ValueError("expires_in は整数で指定してください。")
    return max(MIN_PRESIGNED_EXPIRES_IN, min(MAX_PRESIGNED_EXPIRES_IN, expires_in))


def ensure_recording_consent(detail: EventDetail, *, action: str = "recording_proxy") -> None:
    """公開撮影の同意境界（approved かつ recording_policy == public かつ community.recording_allowed）を検証する。"""
    if (
        detail.status != "approved"
        or detail.recording_policy != RecordingPolicy.PUBLIC
        or not detail.event.community.recording_allowed
    ):
        logger.warning(
            "recording_proxy_url_denied",
            extra={
                "event_type": "recording_proxy_url_denied",
                "event_detail_id": detail.pk,
                "action": action,
                "reason": "consent_not_public",
            },
        )
        raise RecordingConsentError(
            "承認済みかつ撮影公開の同意（recording_policy=public かつ集会の自動撮影許可）がないため利用できません。"
        )


def resolve_private_r2_bucket() -> str:
    """非公開 R2 バケット名を取得し、公開バケットとの分離を強制検証する。"""
    private_bucket = str(getattr(settings, "AWS_PRIVATE_STORAGE_BUCKET_NAME", "") or "").strip()
    public_bucket = str(getattr(settings, "AWS_STORAGE_BUCKET_NAME", "") or "").strip()
    if not private_bucket:
        raise RecordingStorageConfigError("非公開 R2 バケット設定が構成されていません。")
    if public_bucket and private_bucket == public_bucket:
        raise RecordingStorageConfigError("非公開バケットと公開バケットを同一にすることはできません。")
    return private_bucket


_E2E_ALLOWED_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "storage"})


def _is_allowed_e2e_loopback_endpoint(url: str) -> bool:
    if not getattr(settings, "DEBUG", False) or not getattr(settings, "E2E_LOOPBACK_STORAGE", False):
        return False
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return (
        parsed.scheme == "http"
        and parsed.hostname in _E2E_ALLOWED_LOOPBACK_HOSTS
        and parsed.username is None
        and parsed.password is None
        and parsed.path in ("", "/")
        and not parsed.query
        and not parsed.fragment
    )


def _build_r2_s3_client(*, for_presign: bool = False) -> Any:
    endpoint_url = str(getattr(settings, "AWS_S3_ENDPOINT_URL", "") or "").strip()
    presign_endpoint_url = str(getattr(settings, "AWS_S3_PRESIGN_ENDPOINT_URL", "") or "").strip()
    access_key = str(getattr(settings, "AWS_ACCESS_KEY_ID", "") or "").strip()
    secret_key = str(getattr(settings, "AWS_SECRET_ACCESS_KEY", "") or "").strip()
    if not endpoint_url or not access_key or not secret_key:
        raise RecordingStorageConfigError("R2 署名クライアントの認証設定が構成されていません。")

    use_e2e_loopback = _is_allowed_e2e_loopback_endpoint(endpoint_url)
    target_endpoint = endpoint_url
    if for_presign and presign_endpoint_url:
        if not _is_allowed_e2e_loopback_endpoint(presign_endpoint_url):
            raise RecordingStorageConfigError("署名用エンドポイントは E2E ループバック許可リスト外のため使用できません。")
        target_endpoint = presign_endpoint_url
        use_e2e_loopback = True

    if not target_endpoint.startswith("https://") and not use_e2e_loopback:
        raise RecordingStorageConfigError("R2 エンドポイントは https:// である必要があります。")

    client_config = Config(
        signature_version="s3v4",
        s3={"addressing_style": "path"} if use_e2e_loopback else None,
    )
    return boto3.client(
        "s3",
        endpoint_url=target_endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1" if use_e2e_loopback else "auto",
        config=client_config,
    )


def _presign_url(
    client: Any,
    operation: str,
    *,
    params: dict[str, Any],
    expires_in: int,
) -> str:
    url = client.generate_presigned_url(
        operation,
        Params=params,
        ExpiresIn=expires_in,
    )
    if not isinstance(url, str) or not url:
        raise RecordingStorageError("署名 URL の生成に失敗しました。")
    return url


def create_presigned_proxy_upload_url(
    detail: EventDetail,
    *,
    expires_in: int | None = None,
    include_transcript: bool = False,
    transcript_ext: str = "json",
) -> PresignedUploadResult:
    """非公開 R2 バケットへのプロキシ動画 PUT 署名 URL を発給する。"""
    ensure_recording_consent(detail, action="upload_url")
    private_bucket = resolve_private_r2_bucket()
    ttl = clamp_presigned_expires_in(expires_in)
    object_key = validate_proxy_object_key(detail.pk, generate_proxy_object_key(detail.pk))

    client = _build_r2_s3_client(for_presign=True)
    upload_url = _presign_url(
        client,
        "put_object",
        params={
            "Bucket": private_bucket,
            "Key": object_key,
            "ContentType": "video/mp4",
        },
        expires_in=ttl,
    )

    transcript_key: str | None = None
    transcript_url: str | None = None
    transcript_headers: dict[str, str] | None = None
    if include_transcript:
        transcript_key = validate_transcript_object_key(
            detail.pk,
            generate_transcript_object_key(detail.pk, ext=transcript_ext),
        )
        content_type = _TRANSCRIPT_CONTENT_TYPES[transcript_ext]
        transcript_url = _presign_url(
            client,
            "put_object",
            params={
                "Bucket": private_bucket,
                "Key": transcript_key,
                "ContentType": content_type,
            },
            expires_in=ttl,
        )
        transcript_headers = {"Content-Type": content_type}

    expires_at = timezone.now() + timedelta(seconds=ttl)
    logger.info(
        "recording_proxy_upload_url_issued",
        extra={
            "event_type": "recording_proxy_upload_url_issued",
            "event_detail_id": detail.pk,
            "expires_in": ttl,
            "include_transcript": include_transcript,
        },
    )
    return PresignedUploadResult(
        event_detail_id=detail.pk,
        object_key=object_key,
        upload_url=upload_url,
        headers={"Content-Type": "video/mp4"},
        expires_in=ttl,
        expires_at=expires_at,
        transcript_object_key=transcript_key,
        transcript_upload_url=transcript_url,
        transcript_headers=transcript_headers,
    )


def create_presigned_proxy_download_url(
    detail: EventDetail,
    *,
    expires_in: int | None = None,
) -> PresignedDownloadResult:
    """スタッフ画面向けに非公開 R2 プロキシ動画の署名 GET URL を発給する。"""
    ensure_recording_consent(detail, action="download_url")
    review_item = RecordingReviewItem.objects.filter(event_detail=detail).first()
    if review_item is None:
        logger.warning(
            "recording_proxy_url_denied",
            extra={
                "event_type": "recording_proxy_url_denied",
                "event_detail_id": detail.pk,
                "action": "download_url",
                "reason": "proxy_not_found",
            },
        )
        raise RecordingProxyNotFoundError("プロキシ動画がまだ登録されていません。")

    now = timezone.now()
    remaining_sec: int | None = None
    if review_item.proxy_expires_at is not None:
        remaining_sec = int((review_item.proxy_expires_at - now).total_seconds())
    is_deleted_after_upload = (
        not review_item.proxy_r2_key
        and review_item.proxy_uploaded_at is not None
        and review_item.review_status
        not in (RecordingReviewItem.ReviewStatus.UNRECORDED, RecordingReviewItem.ReviewStatus.PROCESSING)
    )
    if (
        review_item.review_status == RecordingReviewItem.ReviewStatus.EXPIRED
        or (remaining_sec is not None and remaining_sec < MIN_PRESIGNED_EXPIRES_IN)
        or is_deleted_after_upload
    ):
        logger.warning(
            "recording_proxy_url_denied",
            extra={
                "event_type": "recording_proxy_url_denied",
                "event_detail_id": detail.pk,
                "action": "download_url",
                "reason": "proxy_expired",
            },
        )
        raise RecordingProxyExpiredError("プロキシ動画の保持期限切れ、または残存時間が 15 分未満です。")

    if not review_item.proxy_r2_key:
        logger.warning(
            "recording_proxy_url_denied",
            extra={
                "event_type": "recording_proxy_url_denied",
                "event_detail_id": detail.pk,
                "action": "download_url",
                "reason": "proxy_not_found",
            },
        )
        raise RecordingProxyNotFoundError("プロキシ動画がまだ登録されていません。")

    validated_key = validate_proxy_object_key(detail.pk, review_item.proxy_r2_key)
    private_bucket = resolve_private_r2_bucket()
    ttl = clamp_presigned_expires_in(expires_in)
    if remaining_sec is not None:
        ttl = min(ttl, remaining_sec)

    client = _build_r2_s3_client(for_presign=True)
    proxy_url = _presign_url(
        client,
        "get_object",
        params={
            "Bucket": private_bucket,
            "Key": validated_key,
        },
        expires_in=ttl,
    )

    expires_at = now + timedelta(seconds=ttl)
    logger.info(
        "recording_proxy_download_url_issued",
        extra={
            "event_type": "recording_proxy_download_url_issued",
            "event_detail_id": detail.pk,
            "expires_in": ttl,
        },
    )
    return PresignedDownloadResult(
        review_item=review_item,
        proxy_url=proxy_url,
        expires_in=ttl,
        expires_at=expires_at,
    )
