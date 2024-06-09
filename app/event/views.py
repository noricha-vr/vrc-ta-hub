import os
import re
from pprint import pprint

from django.contrib import messages
from django.shortcuts import redirect
from django.views.generic import TemplateView, ListView, DetailView
import logging
from community.models import Community
from .models import Event, EventDetail
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

# views.py

from django.shortcuts import render
from django.utils import timezone
from django.views.generic import ListView
from .models import Event
from .forms import EventSearchForm
from django.utils import timezone
from django.views.generic import ListView
from .models import Event, Community
# views.py

from django.utils import timezone
from django.views.generic import ListView
from .models import Event
from .forms import EventSearchForm


class EventListView(ListView):
    model = Event
    template_name = 'event/list.html'
    context_object_name = 'events'
    paginate_by = 50

    def get_queryset(self):
        queryset = super().get_queryset()
        now = timezone.now()
        queryset = queryset.filter(date__gte=now.date()).select_related('community').order_by('date', 'start_time')

        form = EventSearchForm(self.request.GET)
        if form.is_valid():
            query = form.cleaned_data.get('query')
            weekdays = form.cleaned_data.get('weekdays')

            if query:
                queryset = queryset.filter(community__name__icontains=query)

            if weekdays:
                queryset = queryset.filter(weekday__in=weekdays)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = EventSearchForm(self.request.GET or None)
        return context


def extract_video_id(youtube_url):
    """
    YouTube URLからvideo_idを抽出する関数。
    """
    pattern = r'(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/(?:[^\/\n\s]+\/\S+\/|(?:v|e(?:mbed)?)\/|\S*?[?&]v=)|youtu\.be\/)([a-zA-Z0-9_-]{11})'
    match = re.search(pattern, youtube_url)
    if match:
        return match.group(1)
    return None


class EventDetailView(DetailView):
    model = EventDetail
    template_name = 'event/detail.html'
    context_object_name = 'event_detail'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        event_detail = self.get_object()
        context['video_id'] = extract_video_id(event_detail.youtube_url)
        return context


from django.utils import timezone
from datetime import datetime

CALENDAR_API_KEY = os.environ.get('CALENDAR_API_KEY')

from datetime import datetime, timedelta


def import_events(request):
    service = build('calendar', 'v3', developerKey=CALENDAR_API_KEY)

    # カレンダーIDを指定（ここでは例として自分のカレンダーを使用）
    calendar_id = 'fbd1334d10a177831a23dfd723199ab4d02036ae31cbc04d6fc33f08ad93a3e7@group.calendar.google.com'

    # 今日の日付と60日後の日付を取得
    today = datetime.now().date()
    end_date = today + timedelta(days=60)

    # イベントを取得
    events_result = service.events().list(calendarId=calendar_id, singleEvents=True, orderBy='startTime',
                                          timeMin=today.isoformat() + 'T00:00:00Z',
                                          timeMax=end_date.isoformat() + 'T23:59:59Z').execute()
    events = events_result.get('items', [])

    for event in events:
        start_datetime = event['start'].get('dateTime', event['start'].get('date'))
        end_datetime = event['end'].get('dateTime', event['end'].get('date'))
        summary = event['summary'].strip()

        start = datetime.strptime(start_datetime, '%Y-%m-%dT%H:%M:%S%z')
        end = datetime.strptime(end_datetime, '%Y-%m-%dT%H:%M:%S%z')

        # コミュニティーを名前と主催者名で検索
        community = Community.objects.filter(name=summary).first()

        event_str = f"{start} - {end} {summary}"
        logger.info(f"Event: {event_str}")

        if community:
            # 同じ日時の同じコミュニティーのイベントが存在するかチェック
            event_exists = Event.objects.filter(community=community, date=start.date(),
                                                start_time=start.time()).exists()

            if not event_exists:
                # イベントを作成
                event = Event(
                    community=community,
                    date=start.date(),
                    start_time=start.time(),
                    duration=(end - start).total_seconds() // 60,  # 分単位に変換
                    weekday=start.strftime("%a"),
                )
                event.save()
            else:
                messages.warning(request, f"Event already exists: {event_str}")
        else:
            messages.warning(request, f"Community not found: {summary}")

    messages.info(request, f"Events imported successfully. {Event.objects.count()} events imported.")
    return redirect('event:list')
