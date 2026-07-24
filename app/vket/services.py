"""Vketコラボに関するビジネスロジック"""

from dataclasses import dataclass

from event.models import Event, EventDetail
from vket.models import VketParticipation, VketPresentation


@dataclass(frozen=True)
class VketPublicationSyncResult:
    """Vket公開同期の変更有無を返す。"""

    event: Event
    changed_index_data: bool


def get_vket_lock_info(event, *, date=None) -> tuple[bool, str]:
    """Vketコラボ期間中のイベントかどうかを判定し、ロックメッセージを返す。

    イベントの所属Communityがアクティブな VketParticipation を持ち、
    かつイベント日がそのコラボの開催期間内（period_start〜period_end）であれば
    ロック中と判定する。1クエリで判定とメッセージ取得を行う。

    Args:
        event: Event インスタンス
        date: 判定対象日。省略時はイベントの現在日

    Returns:
        (ロック中か, メッセージ) のタプル。ロックされていない場合は (False, "")
    """
    # ロック判定に不要な列まで読むと、列追加直後の古いDBスキーマで 500 になりうるため、
    # メッセージ生成に必要な情報だけを取得する（欠損カラム参照による 500 回避）。
    target_date = date or event.date
    participation = (
        VketParticipation.objects.filter(
            community=event.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
            collaboration__period_start__lte=target_date,
            collaboration__period_end__gte=target_date,
        )
        .values_list(
            "collaboration__name",
            "collaboration__period_start",
            "collaboration__period_end",
        )
        .first()
    )
    if not participation:
        return False, ""
    collab_name, period_start, period_end = participation
    message = (
        f"「{collab_name}」期間中（{period_start}〜{period_end}）"
        f"のため、日時の変更は運営のみ可能です。"
    )
    return True, message


def is_event_locked_by_vket(event) -> bool:
    """Vketコラボ期間中のイベントかどうかを判定する。

    Args:
        event: Event インスタンス

    Returns:
        True の場合、集会管理者からの日時変更・削除をブロックすべき
    """
    locked, _ = get_vket_lock_info(event)
    return locked


def get_vket_lock_message(event) -> str:
    """ロック中のイベントに対する表示メッセージを返す。

    Args:
        event: Event インスタンス

    Returns:
        ロックメッセージ文字列。ロックされていない場合は空文字列
    """
    _, message = get_vket_lock_info(event)
    return message


def _weekday_code(date_value) -> str:
    return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][date_value.weekday()]


def _resolve_publication_event(participation: VketParticipation) -> tuple[Event, bool]:
    existing_event = Event.objects.filter(
        community=participation.community,
        date=participation.confirmed_date,
        start_time=participation.confirmed_start_time,
    ).first()
    if existing_event:
        changed = participation.published_event_id != existing_event.pk
        if changed:
            participation.published_event = existing_event
            participation.save(update_fields=["published_event", "updated_at"])
        return existing_event, changed

    weekday = _weekday_code(participation.confirmed_date)
    if participation.published_event_id:
        event = participation.published_event
        changed = (
            event.date != participation.confirmed_date
            or event.start_time != participation.confirmed_start_time
            or event.duration != participation.confirmed_duration
            or event.weekday != weekday
        )
        if changed:
            # Vket運営同期はユーザーの例外指定ではないため tombstone を作らない。
            event.date = participation.confirmed_date
            event.start_time = participation.confirmed_start_time
            event.duration = participation.confirmed_duration
            event.weekday = weekday
            event.save(update_fields=["date", "start_time", "duration", "weekday"])
        return event, changed

    # Vket運営同期での新規作成は recurrence の例外指定ではない。
    event = Event.objects.create(
        community=participation.community,
        date=participation.confirmed_date,
        start_time=participation.confirmed_start_time,
        duration=participation.confirmed_duration,
        weekday=weekday,
    )
    participation.published_event = event
    participation.save(update_fields=["published_event", "updated_at"])
    return event, True


def sync_participation_publication(
    participation: VketParticipation,
) -> VketPublicationSyncResult:
    """確定済みVket参加を公開用Event/EventDetailへ同期する。"""
    if (
        not participation.confirmed_date
        or not participation.confirmed_start_time
        or not participation.confirmed_duration
    ):
        raise ValueError("confirmed schedule is required for Vket publication sync")

    event, changed_index_data = _resolve_publication_event(participation)

    for presentation in participation.presentations.filter(
        status=VketPresentation.Status.CONFIRMED
    ).select_related("published_event_detail"):
        detail_defaults = {
            "event": event,
            "applicant": participation.applied_by,
            "theme": presentation.theme,
            "speaker": presentation.speaker,
            "start_time": (
                presentation.confirmed_start_time
                or presentation.requested_start_time
                or participation.confirmed_start_time
            ),
            "duration": presentation.duration,
            "detail_type": "LT",
            "status": "approved",
        }

        if presentation.published_event_detail_id:
            detail = presentation.published_event_detail
            dirty_fields = []
            for field_name, value in detail_defaults.items():
                # applied_by の解除（None化）では既存 applicant を保持する。
                # 担当者が外れても登壇者本人の編集導線を失わせないため。非Noneなら Vket 側を正として上書きする。
                if field_name == "applicant" and value is None:
                    continue
                if field_name in {"event", "applicant"}:
                    current_value = getattr(detail, f"{field_name}_id")
                    expected_value = value.pk if value else None
                else:
                    current_value = getattr(detail, field_name)
                    expected_value = value
                if current_value != expected_value:
                    setattr(detail, field_name, value)
                    dirty_fields.append(field_name)
            if dirty_fields:
                detail.save(update_fields=[*dirty_fields, "updated_at"])
                changed_index_data = True
        else:
            detail = EventDetail.objects.create(**detail_defaults)
            presentation.published_event_detail = detail
            presentation.save(update_fields=["published_event_detail", "updated_at"])
            changed_index_data = True

    return VketPublicationSyncResult(event=event, changed_index_data=changed_index_data)


def clear_participation_publication(participation: VketParticipation) -> bool:
    """参加の公開EventDetail連携を解除する。Event自体は保持する。"""
    changed = participation.published_event_id is not None
    detail_ids = list(
        participation.presentations.filter(
            published_event_detail__isnull=False,
        ).values_list("published_event_detail_id", flat=True)
    )
    if detail_ids:
        participation.presentations.filter(
            published_event_detail__isnull=False,
        ).update(published_event_detail=None)
        EventDetail.objects.filter(pk__in=detail_ids).delete()
        changed = True

    if participation.published_event_id:
        participation.published_event = None
        participation.save(update_fields=["published_event", "updated_at"])

    return changed
