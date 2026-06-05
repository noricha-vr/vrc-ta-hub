import json

from django.core.management.base import BaseCommand

from event.slide_reminders import (
    DEFAULT_SLIDE_REMINDER_LIMIT,
    process_slide_publication_reminders,
)


class Command(BaseCommand):
    help = "開催1週間後に資料未公開の発表者へリマインドメールを送信します。"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='送信せず対象件数とIDのみ表示します。',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=DEFAULT_SLIDE_REMINDER_LIMIT,
            help=f'1回で処理する最大件数（デフォルト: {DEFAULT_SLIDE_REMINDER_LIMIT}）。',
        )

    def handle(self, *args, **options):
        result = process_slide_publication_reminders(
            dry_run=options['dry_run'],
            limit=options['limit'],
        )
        self.stdout.write(json.dumps(result.as_dict(), ensure_ascii=False))
