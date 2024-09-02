import datetime
import logging
import re
from datetime import datetime, timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError
from django.db.models import QuerySet
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy, reverse
from django.utils import timezone
from django.views.generic import CreateView, UpdateView, DetailView, ListView
from django.views.generic.edit import DeleteView
from google.auth import default
from google.cloud import bigquery
from googleapiclient.discovery import build

from community.models import Community
from event.forms import EventDetailForm, EventSearchForm, EventCreateForm
from event.libs import convert_markdown, generate_blog
from event.models import EventDetail, Event
from event_calendar.calendar_utils import create_calendar_entry_url
from url_filters import get_filtered_url
from website.settings import GOOGLE_API_KEY, CALENDAR_ID, REQUEST_TOKEN, GEMINI_MODEL

logger = logging.getLogger(__name__)

# デフォルトのクレデンシャルを使用してBigQueryクライアントを設定
credentials, project = default()
client = bigquery.Client(credentials=credentials, project=project, location="asia-northeast1")


class EventCreateView(LoginRequiredMixin, CreateView):
    model = Event
    form_class = EventCreateForm  # フォームクラスを設定
    template_name = 'event/form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['request'] = self.request  # requestオブジェクトを渡す
        return kwargs

    def form_valid(self, form):
        form.instance.weekday = form.instance.date.strftime('%a')
        form.instance.community = Community.objects.filter(custom_user=self.request.user).first()

        try:
            return super().form_valid(form)
        except IntegrityError as e:
            if "Duplicate entry" in str(e):
                message = f"イベントが重複しています: {form.instance.date} {form.instance.start_time}"
                messages.error(self.request, message)
            else:
                raise e
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse_lazy('event:my_list')


class EventDeleteView(LoginRequiredMixin, DeleteView):
    model = Event
    success_url = reverse_lazy('event:my_list')
    template_name = ''  # テンプレートを使用しない場合は空文字列を設定

    def post(self, request, *args, **kwargs):
        message = f"イベントを削除しました: {self.get_object().date} {self.get_object().start_time}"
        messages.success(self.request, message)
        return super().delete(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        return HttpResponseRedirect(self.success_url)


class EventListView(ListView):
    model = Event
    template_name = 'event/list.html'
    context_object_name = 'events'
    paginate_by = 30

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
        context['selected_weekdays'] = self.request.GET.getlist('weekday')
        context['selected_tags'] = self.request.GET.getlist('tags')

        base_url = reverse('event:list')
        current_params = self.request.GET.copy()

        context['weekday_urls'] = {
            choice[0]: get_filtered_url(base_url, current_params, 'weekday', choice[0])
            for choice in context['form'].fields['weekday'].choices
        }
        context['tag_urls'] = {
            choice[0]: get_filtered_url(base_url, current_params, 'tags', choice[0])
            for choice in context['form'].fields['tags'].choices
        }

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
        context['is_discord'] = event_detail.youtube_url.startswith(
            'https://discord.com/') if event_detail.youtube_url else False
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

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['request'] = self.request
        return kwargs

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


from django.contrib import messages
from django.shortcuts import redirect
from django.views.generic import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse

from .models import EventDetail


class GenerateBlogView(LoginRequiredMixin, View):
    def post(self, request, pk):
        try:
            event_detail = EventDetail.objects.get(id=pk)

            if event_detail.event.community.custom_user != request.user:
                messages.error(request, "Invalid request. You don't have permission to perform this action.")
                return redirect('event:detail', pk=event_detail.id)

            text = generate_blog(event_detail, model=GEMINI_MODEL)

            h1 = text.split('\n')[0]
            content = text.replace(h1, '', 1)

            event_detail.h1 = h1.strip().replace('## ', '').replace('# ', '')
            event_detail.contents = content
            event_detail.save()

            messages.success(request, "ブログ記事が生成されました。")
            return redirect('event:detail', pk=event_detail.id)

        except Exception as e:
            messages.error(request, f"エラーが発生しました: {str(e)}")
            return redirect('event:detail', pk=pk)

    def save_to_bigquery(self, pk, video_id, user_id, transcript, prompt, response):
        dataset_id = "web"
        table_name = "event_blog_generation"
        table_id = f"{project}.{dataset_id}.{table_name}"

        # トークン情報を取得
        usage_metadata = response.usage_metadata
        prompt_token_count = usage_metadata.prompt_token_count
        candidates_token_count = usage_metadata.candidates_token_count
        total_token_count = usage_metadata.total_token_count

        rows_to_insert = [
            {
                "timestamp": datetime.now().isoformat(),
                "pk": pk,
                "video_id": video_id,
                "user_id": user_id,
                "transcript": transcript,
                "prompt": prompt,
                "response": response.text,
                "prompt_token_count": prompt_token_count,
                "output_token_count": candidates_token_count,
                "total_token_count": total_token_count
            }
        ]

        errors = client.insert_rows_json(table_id, rows_to_insert)

        if errors == []:
            logger.info(f"New rows have been added to {table_id}")
        else:
            logger.error(f"Encountered errors while inserting rows: {errors}")


class EventDetailDeleteView(LoginRequiredMixin, DeleteView):
    model = EventDetail
    template_name = 'event/detail_confirm_delete.html'

    def get_success_url(self):
        return reverse_lazy('event:my_list')


class EventMyList(LoginRequiredMixin, ListView):
    model = Event
    template_name = 'event/my_list.html'
    context_object_name = 'events'

    def get_queryset(self):
        return Event.objects.filter(community__custom_user=self.request.user).order_by('-date', '-start_time')

    def set_vrc_event_calendar_post_url(self, queryset: QuerySet) -> QuerySet:
        """
        イベントのGoogleフォームのURLを設定する
        """
        for event in queryset:
            if timezone.now().date() > event.date:
                continue
            event.calendar_url = create_calendar_entry_url(event)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['community'] = Community.objects.filter(custom_user=self.request.user).first()
        events = self.set_vrc_event_calendar_post_url(context['events'])
        event_ids = events.values_list('id', flat=True)

        # イベント詳細を取得
        event_details = EventDetail.objects.filter(event_id__in=event_ids).order_by('created_at')

        event_detail_dict = {}
        for detail in event_details:
            if detail.event_id not in event_detail_dict:
                event_detail_dict[detail.event_id] = []
            event_detail_dict[detail.event_id].append(detail)

        for event in events:
            event.detail_list = event_detail_dict.get(event.id, [])

        return context


class EventDetailPastList(ListView):
    template_name = 'event/detail_history.html'
    model = EventDetail
    context_object_name = 'event_details'

    def get_queryset(self):
        queryset = super().get_queryset().filter(
            event__date__lt=timezone.now().date()
        ).order_by('-event__date', '-start_time')

        community_name = self.request.GET.get('community_name', '').strip()
        if community_name:
            queryset = queryset.filter(event__community__name__icontains=community_name)

        speaker = self.request.GET.get('speaker', '').strip()
        if speaker:
            queryset = queryset.filter(speaker__icontains=speaker)

        theme = self.request.GET.get('theme', '').strip()
        if theme:
            queryset = queryset.filter(theme__icontains=theme)

        return queryset
