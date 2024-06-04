from django.views.generic import TemplateView, ListView, DetailView
from .models import Event
from django.http import HttpResponse
from icalendar import Calendar
import requests
from datetime import datetime


class EventListView(ListView):
    model = Event
    template_name = 'event/list.html'
    context_object_name = 'events'


class EventDetailView(DetailView):
    model = Event
    template_name = 'event/detail.html'
    context_object_name = 'event'


def calendar_view(request):
    # 公開カレンダーのURLを指定
    url = "https://calendar.google.com/calendar/ical/fbd1334d10a177831a23dfd723199ab4d02036ae31cbc04d6fc33f08ad93a3e7%40group.calendar.google.com/public/basic.ics"

    # URLからiCalendarデータを取得
    response = requests.get(url)

    # iCalendarデータをパース
    cal = Calendar.from_ical(response.text)

    # 現在の日付を取得
    now = datetime.now().date()

    # イベント情報を取得して表示
    for component in cal.walk():
        if component.name == "VEVENT":
            start = component.get("dtstart").dt
            end = component.get("dtend").dt

            # 現在の日付以降のイベントのみを表示
            if isinstance(start, datetime) and start.date() >= now:
                summary = component.get("summary")
                description = component.get("description")
                print(f"Summary: {summary}")
                print(f"Description: {description}")
                print(f"Start: {start}")
                print(f"End: {end}")
                print("------------------------")

    return HttpResponse("Upcoming events printed in the console.")
