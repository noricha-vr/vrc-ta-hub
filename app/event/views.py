from django.views.generic import TemplateView, ListView, DetailView
from .models import Event
from django.http import HttpResponse
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


class EventListView(ListView):
    model = Event
    template_name = 'event/list.html'
    context_object_name = 'events'


class EventDetailView(DetailView):
    model = Event
    template_name = 'event/detail.html'
    context_object_name = 'event'


def calendar_view(request):
    # Google Calendar APIの認証情報を設定
    creds = Credentials.from_authorized_user_file('path/to/credentials.json',
                                                  ['https://www.googleapis.com/auth/calendar.readonly'])

    # Google Calendar APIクライアントを作成
    service = build('calendar', 'v3', credentials=creds)

    # カレンダーIDを指定
    calendar_id = 'fbd1334d10a177831a23dfd723199ab4d02036ae31cbc04d6fc33f08ad93a3e7@group.calendar.google.com'

    # イベントを取得
    events_result = service.events().list(calendarId=calendar_id, singleEvents=True, orderBy='startTime').execute()
    events = events_result.get('items', [])

    # イベント情報を表示
    for event in events:
        start = event['start'].get('dateTime', event['start'].get('date'))
        end = event['end'].get('dateTime', event['end'].get('date'))
        print(f"Summary: {event['summary']}")
        print(f"Description: {event.get('description', 'N/A')}")
        print(f"Start: {start}")
        print(f"End: {end}")
        print("------------------------")

    return HttpResponse("Calendar events printed in the console.")
