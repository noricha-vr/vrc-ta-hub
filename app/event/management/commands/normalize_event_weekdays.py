"""Event.weekday を開催日由来の固定コードへバッチ単位で正規化する。"""

from collections.abc import Iterable

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


def _merge_counts(
    destination: dict[str, int],
    source: dict[str, int],
) -> None:
    for category in _CATEGORY_KEYS:
        destination[category] += source[category]


def _count_mismatches(
    events: Iterable[Event],
) -> tuple[int, dict[str, int]]:
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
    """Event.weekday を検査し、最大1000件ごとに補正・確定する。

    中断時は再実行で収束させ、最後に ``--check`` の不整合0件を確認する。
    """

    help = (
        'Event.weekdayを最大1000件ずつ確定する。'
        '再実行後の--check不整合0件が完了条件'
    )

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument(
            '--check',
            action='store_true',
            help='完了ゲートとして不整合を分類し、0件なら正常終了する',
        )
        mode.add_argument(
            '--apply',
            action='store_true',
            help='最大1000件ごとに行ロック・補正・commitする。中断時は再実行する',
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
        last_pk = 0
        while True:
            next_pk, batch_changed, batch_counts = self._apply_batch(last_pk)
            if next_pk is None:
                return changed_count, counts
            last_pk = next_pk
            changed_count += batch_changed
            _merge_counts(counts, batch_counts)

    def _apply_batch(
        self,
        last_pk: int,
    ) -> tuple[int | None, int, dict[str, int]]:
        with transaction.atomic():
            events = self._locked_batch(last_pk)
            if not events:
                return None, 0, _empty_counts()
            batch_counts = _empty_counts()
            changed_count = self._normalize_batch(events, batch_counts)
            return events[-1].pk, changed_count, batch_counts

    @staticmethod
    def _locked_batch(last_pk: int) -> list[Event]:
        return list(
            Event.objects.select_for_update()
            .filter(pk__gt=last_pk)
            .only('id', 'date', 'weekday')
            .order_by('pk')[:_BATCH_SIZE]
        )

    @staticmethod
    def _normalize_batch(
        events: list[Event],
        counts: dict[str, int],
    ) -> int:
        changed_events = []
        for event in events:
            expected = weekday_code(event.date)
            category = _classify_weekday(event.weekday, expected)
            if category is None:
                continue
            counts[category] += 1
            event.weekday = expected
            changed_events.append(event)
        if not changed_events:
            return 0
        Event.objects.bulk_update(
            changed_events,
            ['weekday'],
            batch_size=_BATCH_SIZE,
        )
        return len(changed_events)

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
