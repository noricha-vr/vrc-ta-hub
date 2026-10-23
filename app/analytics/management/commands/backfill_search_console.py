from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from analytics.search_console_client import sync_search_console

# Search Console は 16 か月分を保持する。1 日 1 回の API 呼び出しなので上限を決めておく。
MAX_DAYS = 480


class Command(BaseCommand):
    help = 'Search Console の検索語を、指定日（既定は前日）までの N 日分さかのぼって取り込む'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=90, help='さかのぼる日数（既定 90）')
        parser.add_argument('--date', default=None, help='最終日 (YYYY-MM-DD)。省略時は前日。')

    def handle(self, *args, **options):
        days = options['days']
        if not 1 <= days <= MAX_DAYS:
            raise CommandError(f'--days は 1〜{MAX_DAYS} で指定してください')
        try:
            target = date.fromisoformat(options['date']) if options['date'] else (
                timezone.localdate() - timedelta(days=1)
            )
        except ValueError:
            raise CommandError('--date は YYYY-MM-DD で指定してください') from None
        try:
            saved = sync_search_console(target, days=days)
        except RuntimeError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(self.style.SUCCESS(f'SearchConsole backfill: days={days} until={target} saved={saved}'))
