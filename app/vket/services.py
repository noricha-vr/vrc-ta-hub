"""Vketコラボに関するビジネスロジック"""

from dataclasses import dataclass

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from community.constants import weekday_code
from event.models import Event, EventDetail
from ta_hub.index_cache import clear_index_view_cache
from vket.models import VketCollaboration, VketParticipation, VketPresentation

PENDING_DELETIONS_KEY = 'pending_presentation_deletions'


def pending_presentation_deletions(collaboration, participation_id) -> list[int]:
    """次の公開同期で削除する詳細の ID を返す。"""
    settings = collaboration.settings_json
    pending = settings.get(PENDING_DELETIONS_KEY, {}) if isinstance(settings, dict) else {}
    return pending.get(str(participation_id), []) if isinstance(pending, dict) else []


@transaction.atomic
def delete_requested_presentation(presentation: VketPresentation) -> None:
    """希望から発表を消し、公開済みの詳細は次の同期まで保持する。"""
    participation = presentation.participation
    collaboration = VketCollaboration.objects.select_for_update().get(pk=participation.collaboration_id)
    if presentation.published_event_detail_id:
        settings = collaboration.settings_json if isinstance(collaboration.settings_json, dict) else {}
        pending = settings.get(PENDING_DELETIONS_KEY, {})
        pending = dict(pending) if isinstance(pending, dict) else {}
        ids = pending_presentation_deletions(collaboration, participation.pk)
        pending[str(participation.pk)] = sorted(set(ids + [presentation.published_event_detail_id]))
        collaboration.settings_json = {**settings, PENDING_DELETIONS_KEY: pending}
        collaboration.save(update_fields=['settings_json', 'updated_at'])
    presentation.delete()


def _clear_pending_presentation_deletions(participation: VketParticipation) -> bool:
    """この参加が取り下げた公開発表だけを論理削除し、待機情報を消す。"""
    collaboration = VketCollaboration.objects.select_for_update().get(pk=participation.collaboration_id)
    ids = pending_presentation_deletions(collaboration, participation.pk)
    if not ids:
        return False
    # 他集会の詳細や、再び発表と紐づいた詳細は消さない。
    details = EventDetail.objects.filter(
        pk__in=ids, event__community_id=participation.community_id,
        vket_presentations__isnull=True,
    )
    for detail in details:
        detail.delete()
    settings = dict(collaboration.settings_json)
    pending = dict(settings[PENDING_DELETIONS_KEY])
    pending.pop(str(participation.pk), None)
    if pending:
        settings[PENDING_DELETIONS_KEY] = pending
    else:
        settings.pop(PENDING_DELETIONS_KEY, None)
    collaboration.settings_json = settings
    collaboration.save(update_fields=['settings_json', 'updated_at'])
    return True


@transaction.atomic
def apply_pending_presentation_deletions(participation: VketParticipation) -> bool:
    """日程や残りの発表を変更せず、取り下げだけを公開へ反映する。"""
    changed = _clear_pending_presentation_deletions(participation)
    if changed:
        transaction.on_commit(clear_index_view_cache)
    return changed


@transaction.atomic
def confirm_participation_schedule(
    participation: VketParticipation, *, presentation_times=None, use_requested=False,
) -> bool:
    """運営・自動確定共通の日程確定、発表確定、公開同期。変更があれば True。"""
    VketCollaboration.objects.select_for_update().get(pk=participation.collaboration_id)
    if use_requested:
        participation.confirmed_date = participation.requested_date
        participation.confirmed_start_time = participation.requested_start_time
        participation.confirmed_duration = participation.requested_duration
    else:
        # 運営の調整を翌日の自動確定で希望の値に戻さないよう、確定値を今の希望として写す。
        # 主催者が後から希望を変えた時だけ、次の自動確定で反映される。
        participation.requested_date = participation.confirmed_date
        participation.requested_start_time = participation.confirmed_start_time
        participation.requested_duration = participation.confirmed_duration
    participation.schedule_adjusted_by_admin = not use_requested
    participation.progress = VketParticipation.Progress.REHEARSAL
    participation.schedule_confirmed_at = timezone.now()
    participation.save(update_fields=[
        'lifecycle', 'confirmed_date', 'confirmed_start_time', 'confirmed_duration',
        'requested_date', 'requested_start_time', 'requested_duration',
        'admin_note', 'schedule_adjusted_by_admin', 'progress', 'schedule_confirmed_at', 'updated_at',
    ])
    for presentation in participation.presentations.all():
        # 運営の入力は、従来どおり確定済みのこの参加の発表だけに適用する。
        new_time = (presentation_times or {}).get(presentation.pk)
        if use_requested:
            presentation.confirmed_start_time = presentation.requested_start_time
        elif new_time is not None and presentation.status == VketPresentation.Status.CONFIRMED:
            presentation.confirmed_start_time = new_time
        if use_requested or presentation.status == VketPresentation.Status.DRAFT:
            presentation.status = VketPresentation.Status.CONFIRMED
        update_fields = ['confirmed_start_time', 'status', 'updated_at']
        if not use_requested and presentation.confirmed_start_time is not None:
            # 発表時刻も同じく、運営の調整を今の希望として写す
            presentation.requested_start_time = presentation.confirmed_start_time
            update_fields.append('requested_start_time')
        presentation.save(update_fields=update_fields)
    participation._prefetched_objects_cache = {}
    changed = sync_participation_publication(participation).changed_index_data
    if changed:
        transaction.on_commit(clear_index_view_cache)
    return changed


@dataclass(frozen=True)
class VketPublicationSyncResult:
    """Vket公開同期の変更有無を返す。"""

    event: Event
    changed_index_data: bool


def collab_event_match(event, *, date=None) -> Q:
    """イベントがコラボ本体かを判定する VketParticipation 向け条件を返す。

    published_event 一致が本来の判定だが、本番では publication sync が未実施で
    published_event が全件未設定のため、それだけではロックが一切効かない。
    フォールバックとして confirmed 日時一致も本体とみなす（この一致規則は
    _resolve_publication_event が既存 Event を本体として拾う規則と同じ）。

    Args:
        event: Event インスタンス
        date: 追加で本体とみなす日付。移動先日付を渡すと「期間内へ移動して本体になる」
            操作もロック対象にできる（イベントの現在日との OR で判定する）。
    """
    if not event.pk:
        # pk が None だと published_event_id=None が IS NULL に化けて誤判定するため、
        # 未保存イベントは常に不一致（空集合）にする。
        return Q(pk__in=[])
    candidate_dates = {event.date, date} - {None}
    return Q(published_event_id=event.pk) | Q(
        published_event__isnull=True,
        confirmed_date__in=candidate_dates,
        confirmed_start_time=event.start_time,
    )


def get_vket_lock_info(event, *, date=None) -> tuple[bool, str]:
    """Vketコラボ本体のイベントかどうかを判定し、ロックメッセージを返す。

    そのイベント自身がアクティブな VketParticipation のコラボ本体で、
    かつ判定対象日がそのコラボの開催期間内（period_start〜period_end）であれば
    ロック中と判定する。同じ集会の通常イベントは期間内でもロックしない。
    1クエリで判定とメッセージ取得を行う。

    本体判定は published_event 一致、または confirmed 日時一致（下記フォールバック）。

    Args:
        event: Event インスタンス
        date: 判定対象日。省略時はイベントの現在日

    Returns:
        (ロック中か, メッセージ) のタプル。ロックされていない場合は (False, "")
    """
    if not event.pk:
        return False, ""
    # ロック判定に不要な列まで読むと、列追加直後の古いDBスキーマで 500 になりうるため、
    # メッセージ生成に必要な情報だけを取得する（欠損カラム参照による 500 回避）。
    target_date = date or event.date
    participation = (
        VketParticipation.objects.filter(
            # 移動先日付が confirmed_date と一致する場合も本体扱いにして、
            # 期間外の Event を confirmed_date へ動かす操作を素通りさせない。
            collab_event_match(event, date=target_date),
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


def _resolve_publication_event(participation: VketParticipation) -> tuple[Event, bool]:
    existing_event = Event.objects.filter(
        community=participation.community,
        date=participation.confirmed_date,
        start_time=participation.confirmed_start_time,
    ).first()
    if existing_event:
        duration_changed = (
            existing_event.duration != participation.confirmed_duration
        )
        if duration_changed:
            existing_event.duration = participation.confirmed_duration
            existing_event.save(update_fields=["duration"])

        published_event_changed = (
            participation.published_event_id != existing_event.pk
        )
        if published_event_changed:
            participation.published_event = existing_event
            participation.save(update_fields=["published_event", "updated_at"])
        return existing_event, duration_changed or published_event_changed

    weekday = weekday_code(participation.confirmed_date)
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


@transaction.atomic
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

    VketCollaboration.objects.select_for_update().get(pk=participation.collaboration_id)
    deleted_details = _clear_pending_presentation_deletions(participation)
    event, changed_index_data = _resolve_publication_event(participation)
    changed_index_data |= deleted_details

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
            "deleted_at": None,
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


@transaction.atomic
def clear_participation_publication(participation: VketParticipation) -> bool:
    """参加の公開EventDetail連携を解除する。Event自体は保持する。"""
    changed = _clear_pending_presentation_deletions(participation)
    changed |= participation.published_event_id is not None
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
