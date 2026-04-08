import time

from django.core.management.base import BaseCommand
from django.db import connections
from django.db.utils import OperationalError


class Command(BaseCommand):
    help = 'MySQL が接続可能になるまで待機する'

    def add_arguments(self, parser):
        parser.add_argument('--timeout', type=int, default=60)
        parser.add_argument('--interval', type=float, default=2)

    def handle(self, *args, **options):
        timeout = options['timeout']
        interval = options['interval']
        deadline = time.monotonic() + timeout

        while True:
            try:
                connections['default'].cursor()
            except OperationalError as exc:
                if time.monotonic() >= deadline:
                    raise exc
                self.stdout.write('Database unavailable, retrying...')
                time.sleep(interval)
                continue

            self.stdout.write(self.style.SUCCESS('Database available'))
            return
