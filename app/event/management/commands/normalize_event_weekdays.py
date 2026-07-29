"""Event.weekday を開催日由来の固定コードへ正規化する。"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from community.constants import WEEKDAY_CHOICES, weekday_code
from event.models import Event


_CATEGORY_KEYS = (
    'format_only',
    'valid_mismatch',
    'outside_choices',
    'other',
    'empty',
)
_CANONICAL_CODES = {
    code for code, _label in WEEKDAY_CHOICES if code != 'Other'
}
_ALIASES = {
    alias.casefold(): code
    for code, label in WEEKDAY_CHOICES
    if code != 'Other'
    for alias in (code, label)
}
_BATCH_SIZE = 1000


def _classify_weekday(value: str, expected: str) -> str | None:
    if not value:
        return 'empty'
    if value == 'Other':
        return 'other'
    if value in _CANONICAL_CODES:
        return None if value == expected else 'valid_mismatch'
    if _ALIASES.get(value.casefold()) == expected:
        return 'format_only'
    return 'outside_choices'


def _empty_counts() -> dict[str, int]:
    return {category: 0 for category in _CATEGORY_KEYS}


def _count_mismatches(events) -> tuple[int, dict[str, int]]:
    changed_count = 0
    counts = _empty_counts()
    for event in events:
        expected = weekday_code(event.date)
        category = _classify_weekday(event.weekday, expected)
        if category is None:
            continue
        counts[category] += 1
        changed_count += 1
    return changed_count, counts


class Command(BaseCommand):
    """Event.weekday の不整合を検査または一括補正する。"""

    help = 'Event.weekday を Event.date 由来の Mon..Sun に検査・正規化'

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument(
            '--check',
            action='store_true',
            help='不整合を分類して表示し、書き込まない',
        )
        mode.add_argument(
            '--apply',
            action='store_true',
            help='行ロックを取得し、不整合をトランザクション内で一括補正する',
        )

    def handle(self, *args, **options):
        if options['apply']:
            changed_count, counts = self._apply()
            self._write_summary(counts, changed_count, applied=True)
            return

        changed_count, counts = _count_mismatches(
            Event.objects.only('id', 'date', 'weekday').iterator(
                chunk_size=_BATCH_SIZE,
            )
        )
        self._write_summary(counts, changed_count, applied=False)
        if changed_count:
            raise CommandError(
                f'{changed_count}件のEvent.weekday不整合があります'
            )

    def _apply(self) -> tuple[int, dict[str, int]]:
        counts = _empty_counts()
        changed_count = 0
        batch = []
        with transaction.atomic():
            events = (
                Event.objects.select_for_update()
                .only('id', 'date', 'weekday')
                .order_by('id')
                .iterator(
                    chunk_size=_BATCH_SIZE,
                )
            )
            for event in events:
                expected = weekday_code(event.date)
                category = _classify_weekday(event.weekday, expected)
                if category is None:
                    continue
                counts[category] += 1
                changed_count += 1
                event.weekday = expected
                batch.append(event)
                if len(batch) == _BATCH_SIZE:
                    self._bulk_update(batch)
                    batch.clear()
            self._bulk_update(batch)
        return changed_count, counts

    @staticmethod
    def _bulk_update(events: list[Event]) -> None:
        if not events:
            return
        Event.objects.bulk_update(
            events,
            ['weekday'],
            batch_size=_BATCH_SIZE,
        )

    def _write_summary(
        self,
        counts: dict[str, int],
        changed_count: int,
        *,
        applied: bool,
    ) -> None:
        for category in _CATEGORY_KEYS:
            self.stdout.write(f'{category}: {counts[category]}')
        self.stdout.write(f'total_mismatches: {changed_count}')
        if applied:
            self.stdout.write(f'changed: {changed_count}')
