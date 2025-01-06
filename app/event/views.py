import datetime
import logging
import re
from datetime import datetime, timedelta
from typing import List

import requests
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.db import IntegrityError
from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy, reverse
from django.utils import timezone
from django.views.generic import CreateView, UpdateView, DetailView, ListView, FormView
from django.views.generic.edit import DeleteView
from google.auth import default
from google.cloud import bigquery

from community.models import Community, WEEKDAY_CHOICES
from event.forms import EventDetailForm, EventSearchForm, EventCreateForm, GoogleCalendarEventForm
from event.libs import convert_markdown, generate_blog, generate_meta_description
from event.models import EventDetail, Event
from event_calendar.calendar_utils import create_calendar_entry_url
from url_filters import get_filtered_url
from website.settings import GOOGLE_CALENDAR_CREDENTIALS, GOOGLE_CALENDAR_ID, REQUEST_TOKEN, \
    GEMINI_MODEL
from .google_calendar import GoogleCalendarService

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

    def post(self, request, *args, **kwargs):
        event = self.get_object()
        logger.info(
            f"イベント削除開始: ID={event.id}, コミュニティ={event.community.name}, 日付={event.date}, 開始時間={event.start_time}")
        logger.info(f"Google Calendar Event ID: {event.google_calendar_event_id}")

        # 過去のイベントは削除できないようにする
        today = datetime.now().date()
        if event.date < today:
            logger.info(f"過去のイベントの削除が試行されました: ID={event.id}, 日付={event.date}")
            messages.error(request, "過去のイベントは削除できません。")
            return redirect('event:my_list')

        # Googleカレンダーからイベントを削除
        if event.google_calendar_event_id:
            try:
                calendar_service = GoogleCalendarService(
                    calendar_id=GOOGLE_CALENDAR_ID,
                    credentials_path=GOOGLE_CALENDAR_CREDENTIALS
                )
                logger.info(f"Googleカレンダーからの削除を試行: Event ID={event.google_calendar_event_id}")
                calendar_service.delete_event(event.google_calendar_event_id)
                logger.info(f"Googleカレンダーからの削除成功: Event ID={event.google_calendar_event_id}")
                messages.success(request, "イベントをGoogleカレンダーから削除しました。")
            except Exception as e:
                logger.error(
                    f"Googleカレンダーからの削除失敗: Event ID={event.google_calendar_event_id}, エラー={str(e)}")
                messages.error(request, f"Googleカレンダーからの削除中にエラーが発生しました: {str(e)}")
                return redirect('event:my_list')

        # データベースからイベントを削除
        try:
            response = super().delete(request, *args, **kwargs)
            logger.info(f"データベースからの削除成功: ID={event.id}")
            messages.success(request, "イベントを削除しました。")
            return response
        except Exception as e:
            logger.error(f"データベースからの削除失敗: ID={event.id}, エラー={str(e)}")
            messages.error(request, f"データベースからの削除中にエラーが発生しました: {str(e)}")
            return redirect('event:my_list')


class EventListView(ListView):
    model = Event
    template_name = 'event/list.html'
    context_object_name = 'events'
    paginate_by = 30

    def get_queryset(self):
        queryset = super().get_queryset()
        now = timezone.now()
        queryset = queryset.filter(date__gte=now.date()).select_related('community').prefetch_related(
            'details').order_by('date', 'start_time')

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
        context['related_event_details'] = self._fetch_related_event_details(event_detail)

        # コミュニティの開催情報を追加
        community = event_detail.event.community
        context['community_schedule'] = {
            'weekdays': [dict(WEEKDAY_CHOICES)[day] for day in community.weekdays],
            'start_time': community.start_time,
            'end_time': community.end_time,
            'frequency': community.frequency
        }

        return context

    def _fetch_related_event_details(self, event_detail: EventDetail) -> List[EventDetail]:
        # キャッシュキーを生成
        cache_key = f'related_event_details_{event_detail.event_id}'
        related_event_details = cache.get(cache_key)
        related_event_details = None
        if related_event_details is None:
            # キャッシュがない場合のみDBクエリを実行
            related_event_details = list(
                EventDetail.objects
                .filter(
                    event__community=event_detail.event.community,
                    h1__isnull=False,
                    h1__gt=''  # より効率的な空文字列の除外
                )
                .exclude(id=event_detail.id)
                .order_by('-created_at')
                .values('id', 'h1')[:6]
            )

            # 1時間キャッシュする
            cache.set(cache_key, related_event_details, 60 * 60)

        return related_event_details


def sync_calendar_events(request):
    if request.method != 'GET':
        return HttpResponse("Invalid request method.", status=405)

    # Get the Request-Token
    request_token = request.headers.get('Request-Token', '')

    # Check if the token is valid
    # if request_token != REQUEST_TOKEN:
    #     return HttpResponse("Invalid token.", status=403)

    calendar_service = GoogleCalendarService(
        calendar_id=GOOGLE_CALENDAR_ID,
        credentials_path=GOOGLE_CALENDAR_CREDENTIALS
    )

    today = datetime.now()
    end_date = today + timedelta(days=60)

    try:
        calendar_events = calendar_service.list_events(
            time_min=today,
            time_max=end_date,
            max_results=1000  # 十分大きな数を指定
        )

        # データベースのイベントを削除
        delete_outdated_events(calendar_events, today.date())

        # カレンダーイベントを登録/更新
        register_calendar_events(calendar_events)

        logger.info(
            f"Events synchronized successfully. {Event.objects.count()} events found."
        )
        return HttpResponse("Calendar events synchronized successfully.")

    except Exception as e:
        logger.error(f"Failed to sync calendar events: {str(e)}")
        return HttpResponse("Failed to sync calendar events.", status=500)


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
            if (existing_event.duration != (end - start).total_seconds() // 60 or
                    existing_event.google_calendar_event_id != event['id']):
                existing_event.duration = (end - start).total_seconds() // 60
                existing_event.google_calendar_event_id = event['id']
                existing_event.save()
                logger.info(f"Event updated: {event_str}")
        else:
            Event.objects.create(
                community=community,
                date=start.date(),
                start_time=start.time(),
                duration=(end - start).total_seconds() // 60,
                weekday=start.strftime("%a"),
                google_calendar_event_id=event['id']
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
            content = re.sub(r'```\S+', '```', content)

            event_detail.h1 = h1.strip().replace('## ', '').replace('# ', '')
            event_detail.contents = content
            event_detail.meta_description = generate_meta_description(text)
            event_detail.save()

            logger.info(f"ブログ記事が生成されました。: {event_detail.id}")
            logger.info(f"ブログ記事のメタディスクリプション: {event_detail.meta_description}")

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
    paginate_by = 20

    def get(self, request, *args, **kwargs):
        # 通常のget処理の前にページ番号をチェック
        page = request.GET.get('page', 1)
        self.object_list = self.get_queryset()

        paginator = self.get_paginator(self.object_list, self.paginate_by)
        if int(page) > paginator.num_pages and paginator.num_pages > 0:
            # クエリパラメータを維持したまま1ページ目にリダイレクト
            params = request.GET.copy()
            params['page'] = 1
            return redirect(f"{request.path}?{params.urlencode()}")

        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset().filter(
        ).select_related('event', 'event__community').order_by('-event__date', '-start_time')

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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # 現在のGETパラメータを取得
        query_params = self.request.GET.copy()
        if 'page' in query_params:
            del query_params['page']

        context['current_query_params'] = query_params.urlencode()

        return context


class GoogleCalendarEventCreateView(LoginRequiredMixin, FormView):
    template_name = 'event/calendar_form.html'
    form_class = GoogleCalendarEventForm
    success_url = reverse_lazy('event:my_list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # ログインユーザーのコミュニティを初期値として設定
        if self.request.user.is_authenticated:
            community = Community.objects.filter(custom_user=self.request.user).first()
            if community:
                kwargs['initial'] = {
                    'start_time': community.start_time,
                    'duration': community.duration
                }
        return kwargs

    def form_valid(self, form):
        try:
            # フォームのバリデーション後にコミュニティを取得
            community = Community.objects.filter(custom_user=self.request.user).first()
            if not community:
                messages.error(self.request, 'コミュニティが見つかりません')
                return self.form_invalid(form)

            calendar_service = GoogleCalendarService(
                calendar_id=GOOGLE_CALENDAR_ID,
                credentials_path=GOOGLE_CALENDAR_CREDENTIALS
            )

            start_date = form.cleaned_data['start_date']
            start_time = form.cleaned_data['start_time']
            duration = form.cleaned_data['duration']
            recurrence_type = form.cleaned_data['recurrence_type']

            # 開始時刻と終了時刻を設定
            start_datetime = datetime.combine(start_date, start_time)
            end_datetime = start_datetime + timedelta(minutes=duration)

            # 繰り返しルールの設定
            recurrence = None
            if recurrence_type != 'none':
                if recurrence_type == 'weekly':
                    recurrence = [calendar_service._create_weekly_rrule([form.cleaned_data['weekday']])]
                elif recurrence_type == 'biweekly':
                    recurrence = [calendar_service._create_weekly_rrule([form.cleaned_data['weekday']], interval=2)]
                elif recurrence_type == 'monthly_by_date':
                    recurrence = [calendar_service._create_monthly_by_date_rrule([form.cleaned_data['monthly_day']])]
                elif recurrence_type == 'monthly_by_day':
                    # 第何週かを計算
                    week_number = (start_date.day - 1) // 7 + 1
                    recurrence = [calendar_service._create_monthly_by_week_rrule(week_number, form.cleaned_data['weekday'])]

            # Googleカレンダーにイベントを作成
            event = calendar_service.create_event(
                summary=community.name,
                start_time=start_datetime,
                end_time=end_datetime,
                recurrence=recurrence
            )

            if event:
                # イベントの同期を実行
                response = requests.get(
                    f"{self.request.build_absolute_uri('/')[:-1]}/event/sync/",
                    headers={'Request-Token': REQUEST_TOKEN}
                )
                
                if response.status_code == 200:
                    messages.success(self.request, 'イベントが正常に登録されました')
                else:
                    messages.warning(self.request, 'イベントは登録されましたが、同期に失敗しました')
            
            return super().form_valid(form)

        except Exception as e:
            messages.error(self.request, f'イベントの登録に失敗しました: {str(e)}')
            return self.form_invalid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['community'] = Community.objects.filter(custom_user=self.request.user).first()
        return context
