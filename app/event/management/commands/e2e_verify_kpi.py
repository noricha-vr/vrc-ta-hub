"""E2E 検証の外部 KPI を DB 実レコードとモデルメソッドで判定する管理コマンド。"""
from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from event.models import EventDetail, RecordingReviewItem


def evaluate_e2e_kpi(*, detail_id: int, expected_video_id: str) -> dict:
    detail = EventDetail.all_objects.select_related("event__community").filter(pk=detail_id).first()
    if detail is None:
        return {"ok": False, "detail_id": detail_id, "failures": ["event_detail_not_found"], "checks": {}}

    item = RecordingReviewItem.objects.filter(event_detail=detail).first()
    actual_video_id = detail.video_id or ""
    art_state = detail.article_state()
    ready = bool(detail.video_article_ready())

    checks = {
        "review_item_exists": item is not None,
        "review_status_published": bool(
            item is not None and item.review_status == RecordingReviewItem.ReviewStatus.PUBLISHED
        ),
        "public_locked_at_set": bool(item is not None and item.public_locked_at is not None),
        "youtube_url_matches_video_id": bool(expected_video_id and actual_video_id == expected_video_id),
        "article_source_video_id_matches": bool(
            expected_video_id and (detail.article_source_video_id or "") == expected_video_id
        ),
        "article_state_auto": art_state == EventDetail.ArticleState.AUTO,
        "video_article_ready": ready,
    }
    failures = [name for name, passed in checks.items() if not passed]
    return {
        "ok": len(failures) == 0,
        "detail_id": detail_id,
        "expected_video_id": expected_video_id,
        "actual_video_id": actual_video_id,
        "review_status": item.review_status if item is not None else None,
        "article_state": art_state,
        "video_article_ready": ready,
        "checks": checks,
        "failures": failures,
    }


class Command(BaseCommand):
    help = "指定 detail_id の E2E 外部 KPI を検査し、全項目 PASS なら exit 0・1 項目でも欠ければ exit 1 を返す。"

    def add_arguments(self, parser):
        parser.add_argument("--detail-id", type=int, required=True)
        parser.add_argument("--expected-video-id", type=str, required=True)

    def handle(self, *args, **options):
        result = evaluate_e2e_kpi(
            detail_id=options["detail_id"],
            expected_video_id=options["expected_video_id"],
        )
        self.stdout.write(json.dumps(result, ensure_ascii=False))
        if not result["ok"]:
            raise SystemExit(1)
