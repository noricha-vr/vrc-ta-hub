"""EventDetail の記事ステータス判定と遷移リンク解決（Layer 3）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.urls import reverse

from event.models import EventDetail


@dataclass(frozen=True)
class ArticleStatusSummary:
    code: str
    short_label: str
    label: str
    tone: str
    detail_url: str
    edit_url: str | None = None


def can_edit_article_detail(
    user: Any,
    detail: EventDetail,
    *,
    managed_community_ids: set[int] | None = None,
) -> bool:
    """記事・発表詳細の編集画面（detail_update）を開けるか判定する。"""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    is_applicant = (
        detail.detail_type == "LT"
        and detail.status == "approved"
        and detail.applicant_id == getattr(user, "id", None)
    )
    if is_applicant:
        return True
    if managed_community_ids is not None:
        return detail.event.community_id in managed_community_ids
    return bool(detail.event.community.can_edit(user))


def resolve_article_status(
    detail: EventDetail,
    *,
    user: Any = None,
    managed_community_ids: set[int] | None = None,
) -> ArticleStatusSummary:
    """EventDetail の記事ステータス（未作成 / 下書き / 公開済み / NG）と遷移 URL を返す。"""
    detail_url = reverse("event:detail", kwargs={"pk": detail.pk})
    can_edit = can_edit_article_detail(user, detail, managed_community_ids=managed_community_ids)
    edit_url = reverse("event:detail_update", kwargs={"pk": detail.pk}) if can_edit else None

    if detail.is_article_ng:
        return ArticleStatusSummary(
            code="ng",
            short_label="記事化 NG",
            label="記事: 記事化 NG",
            tone="",
            detail_url=detail_url,
            edit_url=edit_url,
        )
    if detail.has_article:
        if detail.status == "approved":
            return ArticleStatusSummary(
                code="published",
                short_label="公開済み",
                label="記事: 公開済み",
                tone="success",
                detail_url=detail_url,
                edit_url=edit_url,
            )
        return ArticleStatusSummary(
            code="draft",
            short_label="下書き（未公開）",
            label="記事: 下書き（未公開）",
            tone="warning",
            detail_url=detail_url,
            edit_url=edit_url,
        )
    if detail.article_generation_requested_at is not None or detail.slide_generate_waiting:
        return ArticleStatusSummary(
            code="draft",
            short_label="下書き（生成待ち）",
            label="記事: 下書き（生成待ち）",
            tone="warning",
            detail_url=detail_url,
            edit_url=edit_url,
        )
    return ArticleStatusSummary(
        code="uncreated",
        short_label="未作成",
        label="記事: 未作成",
        tone="",
        detail_url=detail_url,
        edit_url=edit_url,
    )


def resolve_machine_article_status(detail: EventDetail) -> dict[str, Any]:
    """外部監視クライアント向けの機械判定用記事ステータスを組み立てる。"""
    current_video_id = detail.video_id or ""
    source_video_id = detail.article_source_video_id or ""
    art_state = detail.article_state()
    requested_at = detail.article_generation_requested_at
    last_error = (detail.article_generation_last_error or "").strip()

    if art_state == EventDetail.ArticleState.MANUAL:
        state = "manual"
    elif art_state == EventDetail.ArticleState.AUTO:
        if current_video_id and source_video_id == current_video_id:
            state = "generated_current_video"
        elif requested_at is not None or detail.slide_generate_waiting:
            state = "queued"
        elif last_error:
            state = "failed"
        else:
            state = "generated_pdf_only"
    else:
        if not detail.can_generate_in_queue:
            state = "not_applicable"
        elif requested_at is not None or detail.slide_generate_waiting:
            state = "queued"
        elif last_error:
            state = "failed"
        else:
            state = "not_queued"

    return {
        "detail_id": int(detail.pk),
        "consent": detail.article_consent,
        "state": state,
        "has_video": bool(current_video_id),
        "video_id": current_video_id,
        "article_source_video_id": source_video_id,
        "attempts": int(detail.article_generation_attempts or 0),
        "deferrals": int(detail.article_generation_deferrals or 0),
        "next_attempt_at": requested_at.isoformat() if requested_at is not None else None,
        "article_generated_at": (
            detail.article_generated_at.isoformat() if detail.article_generated_at is not None else None
        ),
        "last_error_code": last_error[:64],
    }
