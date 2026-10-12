"""承認済みの通常応募を、公開IDを変えずにVketへ連携する。"""
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


def application_links(collaboration):
    settings = collaboration.settings_json
    links = settings.get(LINKS_KEY, {}) if isinstance(settings, dict) else {}
    if not isinstance(links, dict):
        return {}
    return {str(key): value for key, value in links.items()
            if str(key).isascii() and str(key).isdigit() and int(key) > 0
            and isinstance(value, int) and not isinstance(value, bool) and value > 0}


def is_normal_presentation(presentation):
    return str(presentation.pk) in application_links(presentation.participation.collaboration)


def inactive_detail(presentation):
    detail = presentation.published_event_detail
    if detail is not None and (detail.deleted_at is not None or detail.status != 'approved'):
        return True

    return False


def lock_application_event(event):
    """承認の詳細ロックより先に、同期と同じ順序でコラボ・参加をロックする。"""
    from vket.services import collab_event_match
    ids = list(VketParticipation.objects.filter(
        collab_event_match(event), community_id=event.community_id,
        lifecycle=VketParticipation.Lifecycle.ACTIVE,
    ).order_by('collaboration_id', 'pk').values_list('pk', 'collaboration_id'))
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


@transaction.atomic
def link_approved_application(detail):
    """名前による既存データ統合はせず、承認された1件だけを参照する。"""
    if detail.status != 'approved' or detail.deleted_at or detail.detail_type != 'LT':
        return
    participations = lock_application_event(detail.event)
    # 1回を複数コラボへ自動採用しない。曖昧な既存データは運営確認に残す。
    if len(participations) > 1:
        logger.warning('application_vket_link_ambiguous', extra={'event_id': detail.event_id})
        extend_event(detail.event)
        return
    participation = participations[0] if participations else None
    if participation:
        links = dict(application_links(participation.collaboration))
        if detail.pk not in links.values():
            presentation = VketPresentation.objects.filter(published_event_detail=detail).first()
            if presentation is None:
                order = (participation.presentations.aggregate(value=Max('order'))['value'] or 0) + 1
                presentation = VketPresentation.objects.create(
                    participation=participation, published_event_detail=detail, order=order,
                    theme=detail.theme, speaker=detail.speaker, duration=detail.duration,
                    requested_start_time=detail.start_time, confirmed_start_time=detail.start_time,
                    status=VketPresentation.Status.CONFIRMED,
                )
            if presentation.participation_id != participation.pk:
                raise ValueError('発表のVket参加が一致しません')
            links[str(presentation.pk)] = detail.pk
            collaboration = participation.collaboration
            settings = dict(collaboration.settings_json) if isinstance(collaboration.settings_json, dict) else {}
            collaboration.settings_json = {**settings, LINKS_KEY: links}
            collaboration.save(update_fields=['settings_json', 'updated_at'])
            if not participation.published_event_id:
                participation.published_event = detail.event
                participation.save(update_fields=['published_event', 'updated_at'])
            logger.info('approved_application_vket_linked', extra={
                'event_detail_id': detail.pk, 'participation_id': participation.pk,
            })
    extend_event(detail.event, participation)


def normal_participations(detail):
    if not detail.pk:
        return []
    return [p for p in VketParticipation.objects.filter(community_id=detail.event.community_id)
            .select_related('collaboration') if detail.pk in application_links(p.collaboration).values()]


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
        candidate = ScheduleBlock(participation.pk, detail.event.community_id, '',
            detail.event.date, detail.start_time, detail.duration)
        if any(blocks_conflict(candidate, block, get_schedule_buffer_minutes(participation.collaboration))
               for block in active_blocks(participation.collaboration, exclude_community_id=detail.event.community_id)):
            warnings.append('他団体のVket発表と時間が重なっています。登録・承認は可能です。')
    start = datetime.combine(detail.event.date, detail.start_time)
    end = start + timedelta(minutes=detail.duration)
    for other in EventDetail.objects.filter(event=detail.event, detail_type='LT', status__in=['pending', 'approved']).exclude(pk=detail.pk):
        other_start = datetime.combine(detail.event.date, other.start_time)
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
