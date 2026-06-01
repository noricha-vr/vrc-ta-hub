from django.core.management.base import BaseCommand, CommandError
from django.http import HttpRequest, QueryDict
from django.conf import settings

from analytics.views import sync_analytics

HTTP_OK = 200


class Command(BaseCommand):
    help = 'Sync page analytics from GA4 (defaults to the previous day)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            dest='date',
            default=None,
            help='取得対象日 (YYYY-MM-DD)。省略時は前日。',
        )

    def handle(self, *args, **options):
        self.stdout.write('Syncing page analytics...')

        # 既存 sync_calendar コマンドに倣い、mock request で view を呼ぶ
        request = HttpRequest()
        request.method = 'GET'
        request.headers = {'Request-Token': settings.REQUEST_TOKEN}
        query = QueryDict(mutable=True)
        if options['date']:
            query['date'] = options['date']
        request.GET = query

        response = sync_analytics(request)
        body = response.content.decode('utf-8')

        # 非200は scheduler / Cloud Run Job 側で失敗検知できるよう異常終了させる
        if response.status_code != HTTP_OK:
            raise CommandError(
                f'Sync failed. Status: {response.status_code}. {body}'
            )

        self.stdout.write(
            self.style.SUCCESS(f'Sync completed. Status: {response.status_code}')
        )
        self.stdout.write(body)
