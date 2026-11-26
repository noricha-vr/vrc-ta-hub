"""録画編集オペレーションの例外・定数・純粋検証ヘルパー（Layer 1）。"""
from __future__ import annotations

import math
import re
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from event.models import RecordingEditOrder, RecordingReviewItem
from event.youtube_urls import youtube_video_id

DEFAULT_PROXY_RETENTION = timedelta(days=30)
ORDER_LEASE_TIMEOUT = timedelta(minutes=30)
STALLED_QUEUED_ORDER_THRESHOLD = timedelta(minutes=30)
STALLED_RUNNING_ORDER_THRESHOLD = ORDER_LEASE_TIMEOUT
MAX_REVISION = 2_147_483_647
_VIDEO_ID_PATTERN = re.compile(r"\A[0-9A-Za-z_-]{11}\Z")

_ALLOWED_YOUTUBE_URL_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
        "www.youtu.be",
        "youtube-nocookie.com",
        "www.youtube-nocookie.com",
    }
)

ALLOWED_COMPLETE_PRIVACIES = frozenset(
    {
        "",
        RecordingReviewItem.YouTubePrivacy.UNLISTED,
        RecordingReviewItem.YouTubePrivacy.PRIVATE,
        RecordingReviewItem.YouTubePrivacy.PUBLIC,
    }
)
ALLOWED_COMPLETE_STATUSES = frozenset(
    {
        "",
        RecordingReviewItem.ReviewStatus.NEEDS_REVIEW,
        RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED,
        RecordingReviewItem.ReviewStatus.PUBLISHED,
        RecordingReviewItem.ReviewStatus.PRIVATE_FIXED,
    }
)


class RecordingOpsError(Exception):
    """録画編集オペレーションサービスの基底例外。"""

    code = "recording_ops_error"

    def __init__(self, detail: str, *, code: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        if code is not None:
            self.code = code


class RecordingValidationError(RecordingOpsError):
    """リクエスト値が仕様を満たさない。"""

    code = "validation_error"


class RecordingNotFoundError(RecordingOpsError):
    """対象レコードが存在しない。"""

    code = "not_found"


class RecordingConflictError(RecordingOpsError):
    """楽観ロックまたは排他状態による競合（HTTP 409）。"""

    code = "conflict"

    def __init__(
        self,
        detail: str,
        *,
        code: str = "conflict",
        current_state: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail, code=code)
        self.current_state = current_state


def serialize_item_state(item: RecordingReviewItem) -> dict[str, Any]:
    """409 競合レスポンス等に含める現在のレビュー状態スナップショット。"""
    return {
        "event_detail_id": item.event_detail_id,
        "review_status": item.review_status,
        "edl_revision": item.edl_revision,
        "edl_json": item.edl_json,
        "youtube_draft_video_id": item.youtube_draft_video_id,
        "youtube_draft_privacy": item.youtube_draft_privacy,
        "is_public_locked": item.is_public_locked,
    }


def derive_review_status_on_success(
    *,
    order_type: str,
    privacy: str,
    requested_review_status: str,
) -> str:
    """完了報告の指示種別・公開設定・要求ステータスから整合するレビュー状態を決定する。"""
    if order_type == RecordingEditOrder.OrderType.STOP_PUBLISH:
        if privacy not in ("", RecordingReviewItem.YouTubePrivacy.PRIVATE):
            raise RecordingValidationError(
                "stop_publish の完了報告では youtube_draft_privacy='private' のみ指定できます。"
            )
        if requested_review_status not in ("", RecordingReviewItem.ReviewStatus.PRIVATE_FIXED):
            raise RecordingValidationError(
                "stop_publish の完了報告では review_status='private_fixed' のみ指定できます。"
            )
        return RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
    if order_type == RecordingEditOrder.OrderType.REOPEN_REVIEW:
        if privacy not in ("", RecordingReviewItem.YouTubePrivacy.PRIVATE):
            raise RecordingValidationError(
                "reopen_review の完了報告では youtube_draft_privacy='private' のみ指定できます。"
            )
        if requested_review_status not in ("", RecordingReviewItem.ReviewStatus.NEEDS_REVIEW):
            raise RecordingValidationError(
                "reopen_review の完了報告では review_status='needs_review' のみ指定できます。"
            )
        return RecordingReviewItem.ReviewStatus.NEEDS_REVIEW
    if (
        requested_review_status == RecordingReviewItem.ReviewStatus.PUBLISHED
        and privacy != RecordingReviewItem.YouTubePrivacy.PUBLIC
    ):
        raise RecordingValidationError("published に遷移するには youtube_draft_privacy='public' が必要です。")
    if (
        privacy == RecordingReviewItem.YouTubePrivacy.PUBLIC
        and requested_review_status
        and requested_review_status != RecordingReviewItem.ReviewStatus.PUBLISHED
    ):
        raise RecordingValidationError(
            "youtube_draft_privacy='public' のときは review_status='published' を指定してください。"
        )
    if (
        requested_review_status == RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
        and privacy != RecordingReviewItem.YouTubePrivacy.PRIVATE
    ):
        raise RecordingValidationError("private_fixed に遷移するには youtube_draft_privacy='private' が必要です。")
    if (
        privacy == RecordingReviewItem.YouTubePrivacy.PRIVATE
        and requested_review_status
        and requested_review_status != RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
    ):
        raise RecordingValidationError(
            "youtube_draft_privacy='private' のときは review_status='private_fixed' を指定してください。"
        )
    if (
        privacy == RecordingReviewItem.YouTubePrivacy.PUBLIC
        or requested_review_status == RecordingReviewItem.ReviewStatus.PUBLISHED
    ):
        return RecordingReviewItem.ReviewStatus.PUBLISHED
    if requested_review_status:
        return requested_review_status
    if privacy == RecordingReviewItem.YouTubePrivacy.PRIVATE:
        return RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
    return RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED


def validate_safe_youtube_url(url: str) -> str:
    """EventDetail.youtube_url へ反映してよい安全な https YouTube URL か検証する。"""
    if not isinstance(url, str) or not url or len(url) > 200:
        raise RecordingValidationError("youtube_url は 200 文字以内の有効な URL で指定してください。")
    if any(ch.isspace() or ch == "\\" for ch in url):
        raise RecordingValidationError("youtube_url に空白・改行・バックスラッシュを含めることはできません。")
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        video_id = youtube_video_id(url)
    except ValueError as exc:
        raise RecordingValidationError("youtube_url の形式が不正です。") from exc
    if parsed.scheme != "https" or hostname not in _ALLOWED_YOUTUBE_URL_HOSTS or parsed.netloc.lower() != hostname:
        raise RecordingValidationError(
            "youtube_url はポートや資格情報を含まない許可済み YouTube https URL で指定してください。"
        )
    if video_id is None:
        raise RecordingValidationError("youtube_url は有効な YouTube 動画 URL である必要があります。")
    return url


def validate_video_id(video_id: str, field_name: str) -> str:
    """YouTube 動画 ID の文字種と長さを検証する。"""
    if not isinstance(video_id, str):
        raise RecordingValidationError(f"{field_name} は文字列で指定してください。")
    if video_id and _VIDEO_ID_PATTERN.fullmatch(video_id) is None:
        raise RecordingValidationError(f"{field_name} の形式が不正です。")
    return video_id


def validate_positive_revision(value: Any, field_name: str) -> int:
    """リビジョン番号が bool でない 1 以上 MAX_REVISION 以下の整数であることを検証する。"""
    if type(value) is not int or value < 1 or value > MAX_REVISION:
        raise RecordingValidationError(f"{field_name} は 1 以上 {MAX_REVISION} 以下の整数で指定してください。")
    return value


def validate_positive_duration(value: Any, field_name: str = "proxy_duration_sec") -> float:
    """再生時間（秒）が bool でない 0 より大きい有限数値であることを検証する。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RecordingValidationError(f"{field_name} は数値で指定してください。")
    try:
        normalized = float(value)
    except OverflowError as exc:
        raise RecordingValidationError(f"{field_name} が表現可能な数値範囲を超えています。") from exc
    if not math.isfinite(normalized) or normalized <= 0:
        raise RecordingValidationError(f"{field_name} は 0 より大きい有限の数値で指定してください。")
    return normalized


def validate_status_privacy_pair(review_status: str, youtube_draft_privacy: str) -> None:
    """レビュー状態と YouTube 下書き公開範囲の組み合わせ整合性を検証する。"""
    if (
        review_status == RecordingReviewItem.ReviewStatus.PRIVATE_FIXED
        and youtube_draft_privacy != RecordingReviewItem.YouTubePrivacy.PRIVATE
    ):
        raise RecordingValidationError(
            "review_status='private_fixed' では youtube_draft_privacy='private' のみ設定できます。"
        )
    if (
        youtube_draft_privacy == RecordingReviewItem.YouTubePrivacy.PRIVATE
        and review_status == RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED
    ):
        raise RecordingValidationError(
            "youtube_draft_privacy='private' と review_status='unlisted_scheduled' は併用できません。"
        )
    if (
        review_status == RecordingReviewItem.ReviewStatus.PUBLISHED
        and youtube_draft_privacy != RecordingReviewItem.YouTubePrivacy.PUBLIC
    ):
        raise RecordingValidationError(
            "review_status='published' では youtube_draft_privacy='public' のみ設定できます。"
        )
