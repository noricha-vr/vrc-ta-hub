import logging
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from event.google_calendar import GoogleCalendarService
from event.models import Event
from website.settings import GOOGLE_CALENDAR_ID, GOOGLE_CALENDAR_CREDENTIALS

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Verify timezone fix by checking upcoming events in the next few hours'

    def handle(self, *args, **options):
        self.stdout.write('Verifying timezone fix...')

        # 現在時刻を取得（タイムゾーン付き）
        now = timezone.now()
        few_hours_later = now + timedelta(hours=6)
        
        self.stdout.write(f'Checking events from {now} to {few_hours_later}')
        
        # 1. データベースから直近のイベントを取得
        db_events = Event.objects.filter(
            date=now.date(),
            start_time__gte=now.time(),
            start_time__lte=few_hours_later.time()
        ).select_related('community')
        
        self.stdout.write(f'Found {db_events.count()} events in database for today')
        for event in db_events:
            self.stdout.write(f'DB Event: {event} (ID: {event.id})')
        
        # 2. Google カレンダーから直近のイベントを取得
        calendar_service = GoogleCalendarService(
            calendar_id=GOOGLE_CALENDAR_ID,
            credentials_path=GOOGLE_CALENDAR_CREDENTIALS
        )
        
        calendar_events = calendar_service.list_events(
            time_min=now,
            time_max=few_hours_later,
            max_results=10
        )
        
        self.stdout.write(f'Found {len(calendar_events)} events in Google Calendar')
        for event in calendar_events:
            start_time = datetime.strptime(
                event['start'].get('dateTime', event['start'].get('date')), 
                '%Y-%m-%dT%H:%M:%S%z'
            )
            end_time = datetime.strptime(
                event['end'].get('dateTime', event['end'].get('date')), 
                '%Y-%m-%dT%H:%M:%S%z'
            )
            
            local_start = start_time.astimezone(timezone.get_current_timezone())
            local_end = end_time.astimezone(timezone.get_current_timezone())
            
            self.stdout.write(f'Google Calendar Event: {event["summary"]}')
            self.stdout.write(f'  - Start: {start_time} (UTC), {local_start} (Local)')
            self.stdout.write(f'  - End: {end_time} (UTC), {local_end} (Local)')
            self.stdout.write(f'  - ID: {event["id"]}')
            
            # 対応するDBのイベントを検索
            matching_events = Event.objects.filter(
                date=local_start.date(),
                start_time=local_start.time(),
                community__name=event['summary'].strip()
            )
            
            if matching_events.exists():
                self.stdout.write(self.style.SUCCESS(f'  ✓ Matching event found in database'))
                for match in matching_events:
                    self.stdout.write(f'    - DB Event ID: {match.id}')
            else:
                self.stdout.write(self.style.ERROR(f'  ✗ No matching event found in database'))
                
                # イベントをデータベースに登録してみる
                from event.views import register_calendar_events
                self.stdout.write('  - Attempting to register this event in database...')
                register_calendar_events([event])
                
                # 再度確認
                if Event.objects.filter(
                    date=local_start.date(),
                    start_time=local_start.time(),
                    community__name=event['summary'].strip()
                ).exists():
                    self.stdout.write(self.style.SUCCESS('  ✓ Successfully registered event in database'))
                else:
                    self.stdout.write(self.style.ERROR('  ✗ Failed to register event in database'))
        
        self.stdout.write(self.style.SUCCESS('Timezone verification complete')) 
