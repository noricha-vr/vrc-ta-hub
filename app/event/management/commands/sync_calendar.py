from django.core.management.base import BaseCommand, CommandError
from django.http import HttpRequest

from event.views import sync_calendar_events
from website.settings import REQUEST_TOKEN

HTTP_OK = 200


class Command(BaseCommand):
    help = 'Sync events from Google Calendar'

    def handle(self, *args, **options):
        self.stdout.write('Syncing calendar events...')

        # Create a mock request
        request = HttpRequest()
        request.method = 'GET'
        request.headers = {'Request-Token': REQUEST_TOKEN}

        # Call the sync function
        response = sync_calendar_events(request)
        body = response.content.decode('utf-8')

        # 非200は cron / Cloud Run Job 側で失敗検知できるよう異常終了させる。
        # CommandError は stderr へ出力され exit code 1 になる（sync_analytics と同じ扱い）
        if response.status_code != HTTP_OK:
            raise CommandError(
                f'Sync failed. Status: {response.status_code}. {body}'
            )

        self.stdout.write(
            self.style.SUCCESS(f'Sync completed. Status: {response.status_code}')
        )
        self.stdout.write(body)
