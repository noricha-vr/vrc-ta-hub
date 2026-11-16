"""開催回の承認済み発表を、公開 ID を変えずに Vket へ連携する。"""
import logging
import re
import unicodedata
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Max, Q

from event.models import Event, EventDetail
from ta_hub.index_cache import clear_index_view_cache
from vket.models import VketCollaboration, VketParticipation, VketPresentation

logger = logging.getLogger(__name__)
LINKS_KEY = 'normal_application_links'
EVENTS_KEY = 'normal_application_event_ids'


def application_links(collaboration):
    settings = collaboration.settings_json
    links = settings.get(LINKS_KEY, {}) if isinstance(settings, dict) else {}
    if not isinstance(links, dict):
        return {}
    return {str(key): value for key, value in links.items()
            if str(key).isascii() and str(key).isdigit() and int(key) > 0
            and isinstance(value, int) and not isinstance(value, bool) and value > 0}


def application_event_ids(collaboration):
    settings = collaboration.settings_json
    values = settings.get(EVENTS_KEY, []) if isinstance(settings, dict) else []
    return [value for value in values if isinstance(value, int) and not isinstance(value, bool) and value > 0] if isinstance(values, list) else []


def is_normal_presentation(presentation):
    return str(presentation.pk) in application_links(presentation.participation.collaboration)


def inactive_detail(presentation):
    detail = presentation.published_event_detail
    if detail is not None and (detail.deleted_at is not None or detail.status != 'approved'):
        return True

    return False


def application_participations(event):
    """取り込みと既存データ確認で、同じ開催回の一致条件を使う。"""
    from vket.services import collab_event_match
    return VketParticipation.objects.filter(
        collab_event_match(event) | Q(
            published_event__isnull=True, confirmed_date__isnull=True,
            requested_date=event.date, requested_start_time=event.start_time,
            collaboration__period_start__lte=event.date, collaboration__period_end__gte=event.date,
        ), community_id=event.community_id,
        lifecycle=VketParticipation.Lifecycle.ACTIVE,
    ).exclude(progress=VketParticipation.Progress.NOT_APPLIED).order_by('collaboration_id', 'pk')


def lock_application_event(event):
    """承認の詳細ロックより先に、同期と同じ順序でコラボ・参加をロックする。"""
    ids = list(application_participations(event).values_list('pk', 'collaboration_id'))
    for collaboration_id in sorted({cid for _, cid in ids}):
        VketCollaboration.objects.select_for_update().get(pk=collaboration_id)
    participations = list(VketParticipation.objects.select_for_update().filter(
        pk__in=[pk for pk, _ in ids],
    ).select_related('collaboration').order_by('pk'))
    Event.objects.select_for_update().get(pk=event.pk)
    return participations


def extend_event(event, participation=None):
    """承認済みの最後の発表終了まで延長する。取消では短縮しない。"""
    start = datetime.combine(event.date, event.start_time)
    minutes = max(event.duration, Event.objects.get(pk=event.pk).duration)
    for detail in EventDetail.objects.filter(event=event, detail_type='LT', status='approved'):
        delta = (datetime.combine(event.date, detail.start_time) - start).total_seconds() / 60
        # 深夜の発表時刻は開催日の翌日。開始前の手動枠は前へ補正する。
        if delta < -720:
            delta += 1440
        minutes = max(minutes, int(delta) + detail.duration)
    if participation:
        minutes = max(minutes, participation.requested_duration or 0, participation.confirmed_duration or 0)
        changed = participation.requested_duration != minutes or participation.confirmed_duration != minutes
        if changed:
            participation.requested_duration = participation.confirmed_duration = minutes
            participation.save(update_fields=['requested_duration', 'confirmed_duration', 'updated_at'])
    if event.duration != minutes:
        old_duration = event.duration
        event.duration = minutes
        event.save(update_fields=['duration'])
        transaction.on_commit(clear_index_view_cache)
        logger.info('approved_application_event_extended', extra={
            'event_id': event.pk, 'old_duration': old_duration, 'duration': minutes,
        })


def matching_presentation(participation, detail):
    """同じ発表で、公開詳細が未設定か論理削除済みの希望を再利用する。"""
    key = (_normalized(detail.speaker), _normalized(detail.theme))
    candidates = participation.presentations.filter(
        Q(published_event_detail__isnull=True) | Q(published_event_detail__deleted_at__isnull=False),
    ).order_by('order', 'pk')
    return next((p for p in candidates if (_normalized(p.speaker), _normalized(p.theme)) == key), None)


def register_normal_link(participation, presentation, detail):
    """対応表と開催回を同じコラボ設定へ記録する。呼出元でコラボをロックする。"""
    collaboration = participation.collaboration
    settings = dict(collaboration.settings_json) if isinstance(collaboration.settings_json, dict) else {}
    links = application_links(collaboration)
    links[str(presentation.pk)] = detail.pk
    collaboration.settings_json = {
        **settings, LINKS_KEY: links,
        EVENTS_KEY: sorted(set(application_event_ids(collaboration) + [detail.event_id])),
    }
    collaboration.save(update_fields=['settings_json', 'updated_at'])


def is_pending_deletion(participation, detail_id):
    """Vket 側で取り下げ、次の同期で消す予定の詳細か。"""
    from vket.services import pending_presentation_deletions
    return detail_id in pending_presentation_deletions(participation.collaboration, participation.pk)


def import_approved_detail(detail_id):
    """保存シグナルのコミット後から呼ぶ。失敗は記録だけして保存元へ伝えない。"""
    try:
        link_approved_application(detail_id)
    except Exception:
        # 保存は確定済み。取り込みは link_unlinked_vket_details でやり直せる。
        event_id = EventDetail.all_objects.filter(pk=detail_id).values_list('event_id', flat=True).first()
        logger.exception('event_detail_vket_import_failed', extra={
            'event_detail_id': detail_id, 'event_id': event_id,
        })


@transaction.atomic
def link_approved_application(detail, *, notify=True, collaboration_id=None):
    """入口を問わず承認済みの詳細を取り込む。保存のコミット後に呼ぶ。"""
    detail_id = detail.pk if isinstance(detail, EventDetail) else detail
    detail = EventDetail.objects.select_related('event').filter(pk=detail_id).first()
    if detail is None or detail.status != 'approved' or detail.detail_type != 'LT':
        return
    # Vket と関係ない回の保存ではロックも開催時間の延長もしない。
    if not application_participations(detail.event).exists():
        return
    participations = lock_application_event(detail.event)
    locked = EventDetail.objects.select_for_update().filter(pk=detail_id).first()
    if (locked is None or locked.event_id != detail.event_id
            or locked.status != 'approved' or locked.detail_type != 'LT'):
        return
    detail = locked
    if len(participations) > 1:
        # 既存の曖昧な回は採用せず、再同期でも公開枠の延長だけを維持する。
        for participation in participations:
            collaboration = participation.collaboration
            settings = dict(collaboration.settings_json) if isinstance(collaboration.settings_json, dict) else {}
            collaboration.settings_json = {
                **settings, EVENTS_KEY: sorted(set(application_event_ids(collaboration) + [detail.event_id])),
            }
            collaboration.save(update_fields=['settings_json', 'updated_at'])
        logger.warning('application_vket_link_ambiguous', extra={'event_id': detail.event_id})
        extend_event(detail.event)
        return
    if VketPresentation.objects.filter(published_event_detail=detail).exists():
        return
    participation = participations[0] if participations else None
    if participation and collaboration_id is not None and participation.collaboration_id != collaboration_id:
        return
    if participation and is_pending_deletion(participation, detail.pk):
        # 取り下げ済みを作り直すと、次の同期が削除を見送ってしまう。
        return
    if participation:
        from vket.activity import activity_snapshot, notify_activity
        before = activity_snapshot(participation) if notify else None
        presentation = matching_presentation(participation, detail)
        created = presentation is None
        if created:
            order = (participation.presentations.aggregate(value=Max('order'))['value'] or 0) + 1
            presentation = VketPresentation(participation=participation, order=order)
        presentation.published_event_detail = detail
        presentation.theme = detail.theme
        presentation.speaker = detail.speaker
        presentation.duration = detail.duration
        presentation.requested_start_time = presentation.confirmed_start_time = detail.start_time
        presentation.status = VketPresentation.Status.CONFIRMED
        presentation.save()
        register_normal_link(participation, presentation, detail)
        if not participation.published_event_id:
            participation.published_event = detail.event
            participation.save(update_fields=['published_event', 'updated_at'])
        logger.info('event_detail_vket_imported', extra={
            'event_detail_id': detail.pk, 'event_id': detail.event_id,
            'participation_id': participation.pk, 'collaboration_id': participation.collaboration_id,
            'presentation_id': presentation.pk, 'presentation_created': created,
        })
        extend_event(detail.event, participation)
        if notify and created:
            notify_activity(participation.collaboration, participation.community.name,
                            before, activity_snapshot(participation), [])
        return presentation


def active_presentations(queryset):
    """公開詳細の論理削除・承認取り消しを、申込みと管理の一覧にも反映する。"""
    return queryset.exclude(published_event_detail__deleted_at__isnull=False).exclude(
        Q(published_event_detail__isnull=False) & ~Q(published_event_detail__status='approved'),
    )


def normal_participations(detail):
    if not detail.pk:
        return []
    return [p for p in VketParticipation.objects.filter(community_id=detail.event.community_id)
            .select_related('collaboration') if detail.pk in application_links(p.collaboration).values()
            or detail.event_id in application_event_ids(p.collaboration)]


def lock_linked_detail(detail):
    participations = normal_participations(detail)
    for cid in sorted({p.collaboration_id for p in participations}):
        VketCollaboration.objects.select_for_update().get(pk=cid)
    for pid in sorted({p.pk for p in participations}):
        VketParticipation.objects.select_for_update().get(pk=pid)
    if participations:
        Event.objects.select_for_update().get(pk=detail.event_id)


@transaction.atomic
def mirror_linked_detail(detail, update_fields=None):
    """本人編集をVketの希望・確定値へ反映。公開同期からの保存は呼出元で除外する。"""
    relevant = {'theme', 'speaker', 'duration', 'start_time', 'event'}
    if update_fields is not None and not relevant.intersection(update_fields):
        return
    presentations = list(VketPresentation.objects.filter(published_event_detail=detail)
                         .select_related('participation__collaboration'))
    for presentation in presentations:
        if not is_normal_presentation(presentation):
            continue
        if inactive_detail(presentation):
            continue
        fields = set(update_fields) if update_fields is not None else {
            'theme', 'speaker', 'duration', 'start_time', 'event',
        }
        changes = {name: getattr(detail, name) for name in ('theme', 'speaker', 'duration') if name in fields}
        if 'start_time' in fields:
            changes.update(requested_start_time=detail.start_time, confirmed_start_time=detail.start_time)
        if 'event' in fields and detail.event_id != presentation.participation.published_event_id:
            # 移動は自動で別参加へ採用しない。元参加の再同期から保護する。
            changes['status'] = VketPresentation.Status.SUBMITTED
        if changes:
            VketPresentation.objects.filter(pk=presentation.pk).update(**changes)
    if detail.status == 'approved' and detail.deleted_at is None:
        for participation in normal_participations(detail):
            extend_event(detail.event, participation if detail.event_id == participation.published_event_id else None)


def application_warnings(detail):
    """重複候補と時間競合は警告のみ。申請中の他者の内容は開示しない。"""
    warnings = []
    candidates = EventDetail.objects.filter(event=detail.event, detail_type='LT',
        status__in=['pending', 'approved']).exclude(pk=detail.pk)
    key = (_normalized(detail.speaker), _normalized(detail.theme))
    if any((_normalized(speaker), _normalized(theme)) == key
           for speaker, theme in candidates.values_list('speaker', 'theme')):
        warnings.append('同じ開催回に同じ発表者・演題の登録があります。内容をご確認ください。登録は可能です。')
    from vket.schedule import ScheduleBlock, active_blocks, blocks_conflict, get_schedule_buffer_minutes
    for participation in VketParticipation.objects.filter(
        Q(published_event=detail.event), lifecycle=VketParticipation.Lifecycle.ACTIVE,
    ).select_related('collaboration'):
        candidate_start = _detail_start(detail.event, detail.start_time)
        candidate = ScheduleBlock(participation.pk, detail.event.community_id, '',
            candidate_start.date(), candidate_start.time(), detail.duration)
        if any(blocks_conflict(candidate, block, get_schedule_buffer_minutes(participation.collaboration))
               for block in active_blocks(participation.collaboration, exclude_community_id=detail.event.community_id)):
            warnings.append('他団体のVket発表と時間が重なっています。登録・承認は可能です。')
    start = _detail_start(detail.event, detail.start_time)
    end = start + timedelta(minutes=detail.duration)
    for other in EventDetail.objects.filter(event=detail.event, detail_type='LT', status__in=['pending', 'approved']).exclude(pk=detail.pk):
        other_start = _detail_start(detail.event, other.start_time)
        if start < other_start + timedelta(minutes=other.duration) and other_start < end:
            warnings.append('同じ開催回の発表と時間が重なっています。登録・承認は可能です。')
            break
    return warnings


def _normalized(value):
    return re.sub(r'[\s\-－–—]', '', unicodedata.normalize('NFKC', value)).casefold()


def vket_input_warnings(participation):
    from vket.schedule import blocks_from, blocks_conflict
    warnings = []
    keys = [(_normalized(p.speaker), _normalized(p.theme)) for p in participation.presentations.all()
            if not inactive_detail(p)]
    if len(keys) != len(set(keys)):
        warnings.append('同じ発表者・演題の登録があります。内容をご確認ください。登録は可能です。')
    blocks = blocks_from([participation], use_requested=True)
    if any(blocks_conflict(a, b) for index, a in enumerate(blocks) for b in blocks[index + 1:]):
        warnings.append('自集会の発表時間が重なっています。登録は可能です。')
    return warnings


def _detail_start(event, start_time):
    start = datetime.combine(event.date, start_time)
    if (start - datetime.combine(event.date, event.start_time)).total_seconds() < -720 * 60:
        start += timedelta(days=1)
    return start
