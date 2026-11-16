"""既存の未連携発表を確認し、明示指定時だけ Vket へ取り込む。"""
import unicodedata

from django.core.management.base import BaseCommand

from event.models import EventDetail
from vket.application_link import (
    application_participations, is_pending_deletion, link_approved_application, matching_presentation,
)


class Command(BaseCommand):
    help = 'Vket の回にある未連携の承認済み発表を一覧表示します（既定は dry-run）。'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='候補を取り込みます。通知は送りません。')
        parser.add_argument('--collaboration', type=int, help='コラボ ID で対象を絞ります。')

    def handle(self, *args, **options):
        candidates = linked = ambiguous = 0
        details = EventDetail.objects.filter(
            status='approved', detail_type='LT', vket_presentations__isnull=True,
        ).select_related('event').order_by('event__community_id', 'event_id', 'pk')
        for detail in details.iterator():
            event = detail.event
            participations = list(application_participations(event).select_related('collaboration'))
            if not participations or (options['collaboration'] is not None and not any(
                p.collaboration_id == options['collaboration'] for p in participations
            )):
                continue
            if any(is_pending_deletion(p, detail.pk) for p in participations):
                # 取り下げ待ちは次の同期で消えるので候補にしない。
                continue
            candidates += 1
            matched = matching_presentation(participations[0], detail) if len(participations) == 1 else None
            state = 'ambiguous' if len(participations) > 1 else detail.status
            ambiguous += len(participations) > 1
            theme = _escape_controls(' '.join(detail.theme.split())[:30])
            self.stdout.write(
                f'community={event.community_id} event={event.pk} detail={detail.pk} '
                f'status={state} start={detail.start_time:%H:%M} theme={theme} '
                f'presentation={matched.pk if matched else "-"}'
            )
            if options['apply'] and len(participations) == 1:
                linked += link_approved_application(
                    detail.pk, notify=False, collaboration_id=options['collaboration'],
                ) is not None
        mode = 'apply' if options['apply'] else 'dry-run'
        self.stdout.write(f'{mode}: candidates={candidates} linked={linked} ambiguous={ambiguous}')


def _escape_controls(text):
    """端末を操作する制御文字（Cc / Cf）を見える表記にする。"""
    return ''.join(
        char.encode('unicode_escape').decode('ascii') if unicodedata.category(char) in ('Cc', 'Cf') else char
        for char in text
    )
