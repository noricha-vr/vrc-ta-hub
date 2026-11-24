"""録画運用サービス共通の行ロック取得および同意有効性判定（Layer 2）。"""
from __future__ import annotations

from community.constants import RecordingPolicy
from community.models import Community
from event.models import EventDetail

from .errors import RecordingNotFoundError


def lock_event_detail(detail_id: int, *, include_deleted: bool = False) -> EventDetail:
    """Community -> EventDetail の統一順で select_for_update ロックして取得する。"""
    community_id = (
        EventDetail.all_objects.filter(pk=detail_id)
        .values_list("event__community_id", flat=True)
        .first()
    )
    if community_id is None:
        raise RecordingNotFoundError("対象の発表が見つかりません。")
    Community.objects.select_for_update().filter(pk=community_id).first()

    manager = EventDetail.all_objects if include_deleted else EventDetail.objects
    qs = manager.select_for_update(of=("self",)).select_related("event__community")
    try:
        return qs.get(pk=detail_id)
    except EventDetail.DoesNotExist as exc:
        raise RecordingNotFoundError("対象の発表が見つかりません。") from exc


def lock_event_detail_or_none(detail_id: int, *, include_deleted: bool = False) -> EventDetail | None:
    """Community -> EventDetail の統一順でロック取得し、存在しない場合は None を返す。"""
    try:
        return lock_event_detail(detail_id, include_deleted=include_deleted)
    except RecordingNotFoundError:
        return None


def is_recording_consent_valid(detail: EventDetail) -> bool:
    """発表が録画公開同意および集会許可を満たしており未削除かを返す。"""
    return (
        detail.deleted_at is None
        and detail.recording_policy == RecordingPolicy.PUBLIC
        and bool(detail.event.community.recording_allowed)
    )
