from django.core.management.base import BaseCommand
from django.http import HttpRequest
from event.views import sync_calendar_events
from website.settings import REQUEST_TOKEN

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
        
        # Output the result
        self.stdout.write(self.style.SUCCESS(f'Sync completed. Status: {response.status_code}'))
        self.stdout.write(response.content.decode('utf-8')) 
