"""希望の日程・発表を申込み順に自動確定する。"""
import logging

from django.db import transaction
from django.db.models import F, Prefetch, prefetch_related_objects

from .activity import notify_auto_confirmation
from .models import VketCollaboration, VketParticipation, VketPresentation
from .schedule import (
    ScheduleBlock, blocks_conflict, blocks_from, format_pair, get_schedule_buffer_minutes,
    is_fully_confirmed,
)
from .services import (
    apply_pending_presentation_deletions, confirm_participation_schedule,
    pending_presentation_deletions,
)

logger = logging.getLogger(__name__)
AUTO_CONFIRM_PHASES = (
    VketCollaboration.Phase.ENTRY_OPEN, VketCollaboration.Phase.SCHEDULING,
    VketCollaboration.Phase.LT_COLLECTION, VketCollaboration.Phase.ANNOUNCEMENT,
)


def _needs_confirmation(participation, collaboration) -> bool:
    """日程・発表・公開情報の希望との差、または公開発表の削除待ちがあるか。"""
    if not is_fully_confirmed(participation):
        return True
    if (participation.requested_date, participation.requested_start_time, participation.requested_duration) != (
        participation.confirmed_date, participation.confirmed_start_time, participation.confirmed_duration,
    ):
        return True
    if not participation.published_event_id or pending_presentation_deletions(collaboration, participation.pk):
        return True
    event = participation.published_event
    # 管理画面などで公開イベントの日時だけがずれた時も、確定値で公開し直す
    if (event.date, event.start_time, event.duration) != (
        participation.confirmed_date, participation.confirmed_start_time, participation.confirmed_duration,
    ):
        return True
    for presentation in participation.presentations.all():
        detail = presentation.published_event_detail
        if (
            presentation.status != VketPresentation.Status.CONFIRMED
            or presentation.requested_start_time != presentation.confirmed_start_time
            or detail is None
            or detail.deleted_at is not None
            or detail.event_id != participation.published_event_id
            or detail.event.date != participation.confirmed_date
            or detail.start_time != presentation.confirmed_start_time
            or (presentation.speaker, presentation.theme, presentation.duration) != (
                detail.speaker, detail.theme, detail.duration,
            )
        ):
            return True
    return False


def _confirmed_blocks(participations) -> list[ScheduleBlock]:
    """変更待ちも含め、希望の変更前の確定・公開済みの発表枠を返す。"""
    blocks = []
    for participation in participations:
        if not is_fully_confirmed(participation):
            continue
        for presentation in participation.presentations.all():
            detail = presentation.published_event_detail
            day = participation.confirmed_date
            if detail is not None and detail.deleted_at is None:
                # 公開中の枠は、公開イベントの日付・時刻・長さで押さえる（確定値とずれていても公開側を守る）
                day, start, duration = detail.event.date, detail.start_time, detail.duration
            elif presentation.status == VketPresentation.Status.CONFIRMED:
                start, duration = presentation.confirmed_start_time, presentation.duration
            else:
                continue
            if start is None or not duration:
                continue
            blocks.append(ScheduleBlock(
                participation_id=participation.pk, community_id=participation.community_id,
                community_name=participation.community.name, date=day,
                start=start, duration=duration, is_confirmed=True, presentation_id=presentation.pk,
            ))
    return blocks


@transaction.atomic
def _confirm_collaboration(collaboration_id) -> dict:
    """同じコラボの呼出し・主催者編集・運営確定を行ロックで直列化する。"""
    collaboration = VketCollaboration.objects.select_for_update().get(pk=collaboration_id)
    result = {'confirmed': 0, 'skipped': 0, 'incomplete': 0}
    if collaboration.phase not in AUTO_CONFIRM_PHASES:
        return result
    participations = list(
        VketParticipation.objects.select_for_update().filter(
            collaboration=collaboration, lifecycle=VketParticipation.Lifecycle.ACTIVE,
        ).exclude(progress=VketParticipation.Progress.NOT_APPLIED)
        .order_by(F('applied_at').asc(nulls_last=True), 'pk')
    )
    # JOIN 先の nullable FK は行ロックせず、参加の取得後に関連を読む。
    prefetch_related_objects(
        participations, 'community', 'published_event',
        Prefetch('presentations', queryset=VketPresentation.objects.select_related('published_event_detail__event')),
    )
    targets = [p for p in participations if _needs_confirmation(p, collaboration)]
    # 取り下げは日程の成否によらず反映し、変更待ちの旧公開枠は確保しておく。
    for participation in targets:
        apply_pending_presentation_deletions(participation)
    accepted_blocks = _confirmed_blocks(participations)
    buffer_minutes = get_schedule_buffer_minutes(collaboration)
    confirmed_lines, skipped_lines = [], []
    for participation in targets:
        presentations = list(participation.presentations.all())
        if (
            participation.requested_date is None
            or participation.requested_start_time is None
            or not participation.requested_duration
            or any(p.requested_start_time is None or not p.duration for p in presentations)
        ):
            result['incomplete'] += 1
            continue
        candidates = blocks_from([participation], use_requested=True)
        other_blocks = [b for b in accepted_blocks if b.participation_id != participation.pk]
        pairs = [
            (a, b) for a in candidates for b in other_blocks
            if a.community_id != b.community_id and blocks_conflict(a, b, buffer_minutes)
        ]
        if pairs:
            result['skipped'] += 1
            skipped_lines.extend(format_pair(a, b) for a, b in pairs)
            continue
        confirm_participation_schedule(participation, use_requested=True)
        accepted_blocks = other_blocks + candidates
        result['confirmed'] += 1
        confirmed_lines.append(
            f'{participation.community.name} {participation.confirmed_date:%Y/%m/%d} '
            f'{participation.confirmed_start_time:%H:%M}（{participation.confirmed_duration}分）'
        )
    notify_auto_confirmation(collaboration, confirmed_lines, skipped_lines)
    logger.info('Vket日程の自動確定結果', extra={'collaboration_id': collaboration_id, **result})
    return result


def auto_confirm_schedules() -> dict:
    """受付中〜告知のコラボを処理し、認証済みの呼出元へ結果の件数を返す。"""
    totals = {'confirmed': 0, 'skipped': 0, 'incomplete': 0}
    ids = VketCollaboration.objects.filter(phase__in=AUTO_CONFIRM_PHASES).order_by('pk').values_list('pk', flat=True)
    for collaboration_id in ids:
        result = _confirm_collaboration(collaboration_id)
        for key in totals:
            totals[key] += result[key]
    return totals
