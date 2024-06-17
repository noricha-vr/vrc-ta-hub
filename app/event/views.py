import os
import re

from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import CreateView, UpdateView, DeleteView
from .models import Event, EventDetail
from event.forms import EventDetailForm

from django.contrib import messages
from django.shortcuts import redirect
from django.views.generic import DetailView
import logging
from .models import EventDetail, Community
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

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
            name = form.cleaned_data.get('name')
            weekdays = form.cleaned_data.get('weekday')

            if name:
                queryset = queryset.filter(community__name__icontains=name)

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
    if not youtube_url:
        return None
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

import logging
from datetime import datetime, timedelta

from django.http import HttpResponse
from django.utils import timezone
from googleapiclient.discovery import build

from .models import Event, Community

logger = logging.getLogger(__name__)

CALENDAR_API_KEY = os.environ.get('CALENDAR_API_KEY')
REQUEST_TOKEN = os.environ.get('REQUEST_TOKEN')


def sync_calendar_events(request):
    if request.method != 'GET':
        return HttpResponse("Invalid request method.", status=405)

    service = build('calendar', 'v3', developerKey=CALENDAR_API_KEY)
    calendar_id = 'fbd1334d10a177831a23dfd723199ab4d02036ae31cbc04d6fc33f08ad93a3e7@group.calendar.google.com'
    today = datetime.now().date()
    end_date = today + timedelta(days=60)

    events_result = service.events().list(
        calendarId=calendar_id,
        singleEvents=True,
        orderBy='startTime',
        timeMin=today.isoformat() + 'T00:00:00Z',
        timeMax=end_date.isoformat() + 'T23:59:59Z',
    ).execute()
    calendar_events = events_result.get('items', [])

    # データベースのイベントを削除
    delete_outdated_events(calendar_events, today)

    # カレンダーイベントを登録/更新
    register_calendar_events(calendar_events)

    logger.info(
        f"Events synchronized successfully. {Event.objects.count()} events found."
    )
    return HttpResponse("Calendar events synchronized successfully.")


def delete_outdated_events(calendar_events, today):
    """データベースに存在するが、カレンダーに存在しないイベントは削除"""
    future_events = Event.objects.filter(date__gte=today).values(
        'id', 'community__name', 'date', 'start_time'
    )

    for db_event in future_events:
        db_event_datetime = datetime.combine(
            db_event['date'], db_event['start_time']
        ).astimezone(timezone.utc)
        db_event_str = f"{db_event_datetime.isoformat()} {db_event['community__name']}"

        found = any(
            datetime.strptime(
                e['start'].get('dateTime', e['start'].get('date')), '%Y-%m-%dT%H:%M:%S%z'
            ).astimezone(timezone.utc)
            == db_event_datetime
            and e['summary'].strip() == db_event['community__name']
            for e in calendar_events
        )

        if not found:
            Event.objects.filter(id=db_event['id']).delete()
            logger.info(f"Event deleted: {db_event_str}")


def register_calendar_events(calendar_events):
    """カレンダーイベントの登録処理"""
    for event in calendar_events:
        start_datetime = event['start'].get('dateTime', event['start'].get('date'))
        end_datetime = event['end'].get('dateTime', event['end'].get('date'))
        summary = event['summary'].strip()

        start = datetime.strptime(start_datetime, '%Y-%m-%dT%H:%M:%S%z')
        end = datetime.strptime(end_datetime, '%Y-%m-%dT%H:%M:%S%z')

        community = Community.objects.filter(name=summary).first()
        if not community:
            logger.warning(f"Community not found: {summary}")
            continue

        event_str = f"{start} - {end} {summary}"
        logger.info(f"Event: {event_str}")

        existing_event = Event.objects.filter(
            community=community, date=start.date(), start_time=start.time()
        ).first()

        if existing_event:
            if existing_event.duration != (end - start).total_seconds() // 60:
                existing_event.duration = (end - start).total_seconds() // 60
                existing_event.save()
                logger.info(f"Event updated: {event_str}")
        else:
            Event.objects.create(
                community=community,
                date=start.date(),
                start_time=start.time(),
                duration=(end - start).total_seconds() // 60,
                weekday=start.strftime("%a"),
            )
            logger.info(f"Event created: {event_str}")


class EventDetailCreateView(LoginRequiredMixin, CreateView):
    model = EventDetail
    form_class = EventDetailForm
    template_name = 'event/detail_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.event = get_object_or_404(Event, pk=kwargs['event_pk'])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.event = self.event
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('event:detail', kwargs={'pk': self.object.pk})


class EventDetailUpdateView(LoginRequiredMixin, UpdateView):
    model = EventDetail
    form_class = EventDetailForm
    template_name = 'event/detail_form.html'

    def get_success_url(self):
        return reverse_lazy('event:detail', kwargs={'pk': self.object.pk})


class EventDetailDeleteView(LoginRequiredMixin, DeleteView):
    model = EventDetail
    template_name = 'event/detail_confirm_delete.html'

    def get_success_url(self):
        return reverse_lazy('event:detail_list')


from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView
from .models import Event


class EventMyList(LoginRequiredMixin, ListView):
    model = Event
    template_name = 'event/my_list.html'
    context_object_name = 'events'

    def get_queryset(self):
        return Event.objects.filter(community__custom_user=self.request.user).prefetch_related('details').order_by(
            '-date', '-start_time')
