import os
import re
import logging
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, UpdateView, DeleteView, DetailView, ListView
from django.http import HttpResponse
from django.utils import timezone
from django.contrib.auth.mixins import LoginRequiredMixin
from event.libs import convert_markdown, get_transcript, genai_model, create_blog_prompt
from event.forms import EventDetailForm, EventSearchForm
from event.models import EventDetail, Event
from community.models import Community
from datetime import datetime, timedelta
from googleapiclient.discovery import build
from website.settings import GOOGLE_API_KEY, CALENDAR_ID, REQUEST_TOKEN

logger = logging.getLogger(__name__)


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
            if name := form.cleaned_data.get('name'):
                queryset = queryset.filter(community__name__icontains=name)

            if weekdays := form.cleaned_data.get('weekday'):
                queryset = queryset.filter(weekday__in=weekdays)

            if tags := form.cleaned_data['tags']:
                for tag in tags:
                    queryset = queryset.filter(community__tags__contains=[tag])

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
        context['html_content'] = convert_markdown(event_detail.contents)
        return context


def sync_calendar_events(request):
    if request.method != 'GET':
        return HttpResponse("Invalid request method.", status=405)

    # Get the Request-Token
    request_token = request.headers.get('Request-Token', '')

    # Check if the token is valid
    if request_token != REQUEST_TOKEN:
        return HttpResponse("Invalid token.", status=403)

    service = build('calendar', 'v3', developerKey=GOOGLE_API_KEY)
    today = datetime.now().date()
    end_date = today + timedelta(days=60)

    events_result = service.events().list(
        calendarId=CALENDAR_ID,
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


class GenerateBlogView(LoginRequiredMixin, View):
    def post(self, request, pk):  # request を引数に追加
        event_detail = EventDetail.objects.get(id=pk)

        # ユーザーとイベントの所有者が同じかを確認
        if event_detail.event.community.custom_user != request.user:
            return HttpResponse("Invalid request.", status=403)
        # URLから動画IDを抽出
        video_id = extract_video_id(event_detail.youtube_url)
        if not video_id:
            return HttpResponse(f"Invalid YouTube URL. {event_detail.youtube_url}", status=400)
        prompt = create_blog_prompt(event_detail)
        # 文字起こしを取得
        transcript = get_transcript(video_id)
        response = genai_model.generate_content(prompt + transcript, stream=False)
        event_detail.contents = response.text
        event_detail.save()
        return redirect('event:detail', pk=event_detail.id)


class EventDetailDeleteView(LoginRequiredMixin, DeleteView):
    model = EventDetail
    template_name = 'event/detail_confirm_delete.html'

    def get_success_url(self):
        return reverse_lazy('event:detail_list')


class EventMyList(LoginRequiredMixin, ListView):
    model = Event
    template_name = 'event/my_list.html'
    context_object_name = 'events'

    def get_queryset(self):
        return Event.objects.filter(community__custom_user=self.request.user).order_by('-date', '-start_time')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        events = context['events']
        event_ids = events.values_list('id', flat=True)
        event_details = EventDetail.objects.filter(event_id__in=event_ids).order_by('-created_at')

        event_detail_dict = {}
        for detail in event_details:
            if detail.event_id not in event_detail_dict:
                event_detail_dict[detail.event_id] = []
            event_detail_dict[detail.event_id].append(detail)

        for event in events:
            event.detail_list = event_detail_dict.get(event.id, [])

        return context
