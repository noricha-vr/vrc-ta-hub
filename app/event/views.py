import datetime
import logging
import re
from datetime import datetime, timedelta
from typing import List

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

from django.contrib import messages
from django.shortcuts import redirect
from django.views.generic import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse

from .models import EventDetail
from community.models import Community, WEEKDAY_CHOICES
from event.forms import EventDetailForm, EventSearchForm, EventCreateForm, GoogleCalendarEventForm
from event.libs import convert_markdown, generate_blog, generate_meta_description
from event.models import EventDetail, Event
from event_calendar.calendar_utils import create_calendar_entry_url, generate_google_calendar_url
from url_filters import get_filtered_url
from website.settings import DEBUG, GOOGLE_CALENDAR_CREDENTIALS, GOOGLE_CALENDAR_ID, REQUEST_TOKEN, \
    GEMINI_MODEL
from .google_calendar import GoogleCalendarService

logger = logging.getLogger(__name__)

# Google認証とBigQueryをモック化
import os
if os.environ.get('TESTING'):
    from unittest.mock import MagicMock
    credentials = MagicMock()
    project = 'test-project'
    client = MagicMock()
else:
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
        
        # ユーザーが所有するコミュニティを取得
        user_community = Community.objects.filter(custom_user=request.user).first()
        
        # イベントが自分のコミュニティのものでない場合は削除を許可しない
        if not user_community or event.community != user_community:
            messages.error(request, "このイベントを削除する権限がありません。")
            return redirect('event:my_list')
            
        logger.info(
            f"イベント削除開始: ID={event.id}, コミュニティ={event.community.name}, 日付={event.date}, 開始時間={event.start_time}")
        logger.info(f"Google Calendar Event ID: {event.google_calendar_event_id}")

        # 以降のイベントも削除するかどうかのチェック
        delete_subsequent = request.POST.get('delete_subsequent') == 'on'
        events_to_delete = [event]

        if delete_subsequent:
            # 同じコミュニティの、選択したイベント以降のイベントを取得
            # ユーザーのコミュニティのイベントのみに制限
            subsequent_events = Event.objects.filter(
                community=user_community,
                date__gt=event.date
            ).order_by('date', 'start_time')
            events_to_delete.extend(subsequent_events)
            logger.info(f"以降のイベントも削除します: {len(subsequent_events)}件")

        success_count = 0
        error_count = 0

        for event_to_delete in events_to_delete:
            try:
                # Googleカレンダーからイベントを削除
                if event_to_delete.google_calendar_event_id:
                    try:
                        calendar_service = GoogleCalendarService(
                            calendar_id=GOOGLE_CALENDAR_ID,
                            credentials_path=GOOGLE_CALENDAR_CREDENTIALS
                        )
                        logger.info(f"Googleカレンダーからの削除を試行: Event ID={event_to_delete.google_calendar_event_id}")
                        calendar_service.delete_event(event_to_delete.google_calendar_event_id)
                        logger.info(f"Googleカレンダーからの削除成功: Event ID={event_to_delete.google_calendar_event_id}")
                    except Exception as e:
                        logger.error(
                            f"Googleカレンダーからの削除失敗: Event ID={event_to_delete.google_calendar_event_id}, エラー={str(e)}")
                        error_count += 1
                        continue

                # データベースからイベントを削除
                event_to_delete.delete()
                success_count += 1
                logger.info(f"データベースからの削除成功: ID={event_to_delete.id}")

            except Exception as e:
                logger.error(f"イベントの削除に失敗: ID={event_to_delete.id}, エラー={str(e)}")
                error_count += 1

        if success_count > 0:
            if delete_subsequent:
                messages.success(request, f"{success_count}件のイベントを削除しました。")
            else:
                messages.success(request, "イベントを削除しました。")

        if error_count > 0:
            messages.error(request, f"{error_count}件のイベントの削除中にエラーが発生しました。")

        return redirect('event:my_list')


class EventListView(ListView):
    model = Event
    template_name = 'event/list.html'
    context_object_name = 'events'
    paginate_by = 30

    def get(self, request, *args, **kwargs):
        # 通常のget処理の前にページ番号をチェック
        page_str = request.GET.get('page', '1')
        
        try:
            # ページ番号のみを抽出（数字以外を除去）
            page = int(''.join(filter(str.isdigit, page_str)) or '1')
        except (ValueError, TypeError):
            # 無効なページ番号の場合は1ページ目にリダイレクト
            params = request.GET.copy()
            params['page'] = '1'
            return redirect(f"{request.path}?{params.urlencode()}")

        self.object_list = self.get_queryset()
        paginator = self.get_paginator(self.object_list, self.paginate_by)

        if page > paginator.num_pages and paginator.num_pages > 0:
            # 存在しないページ番号の場合は1ページ目にリダイレクト
            params = request.GET.copy()
            params['page'] = '1'
            return redirect(f"{request.path}?{params.urlencode()}")

        # ページ番号が有効な場合は通常の処理を続行
        request.GET = request.GET.copy()
        request.GET['page'] = str(page)
        return super().get(request, *args, **kwargs)

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

        # 各イベントにGoogleカレンダー追加用URLを設定
        for event in queryset:
            event.google_calendar_url = generate_google_calendar_url(self.request, event)

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

    calendar_service = GoogleCalendarService(
        calendar_id=GOOGLE_CALENDAR_ID,
        credentials_path=GOOGLE_CALENDAR_CREDENTIALS
    )

    today = datetime.now()
    end_date = today + timedelta(days=60)

    try:
        logger.info(f'カレンダー同期開始: 期間={today} から {end_date}')
        calendar_events = calendar_service.list_events(
            time_min=today,
            time_max=end_date,
            max_results=1000  # 十分大きな数を指定
        )
        logger.info(f'Googleカレンダーからのイベント取得成功: {len(calendar_events)}件')

        # データベースのイベントを削除
        delete_outdated_events(calendar_events, today.date())
        logger.info('古いイベントの削除完了')

        # カレンダーイベントを登録/更新
        register_calendar_events(calendar_events)
        logger.info('イベントの登録/更新完了')

        logger.info(
            f"同期完了: 現在のイベント総数={Event.objects.count()}件"
        )
        return HttpResponse("Calendar events synchronized successfully.")

    except Exception as e:
        logger.error(f"同期失敗: {str(e)}", exc_info=True)
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
        response = super().form_valid(form)
        
        # PDFまたは動画がセットされていて、タイトルが空の場合は自動生成
        if (form.instance.slide_file or form.instance.youtube_url) and not form.instance.meta_description:
            try:
                blog_output = generate_blog(form.instance, model=GEMINI_MODEL)
                form.instance.h1 = blog_output.title
                form.instance.contents = blog_output.text
                form.instance.meta_description = blog_output.meta_description
                form.instance.save()
                messages.success(self.request, "記事を自動生成しました。")
                logger.info(f"記事を自動生成しました: {form.instance.id}")
            except Exception as e:
                logger.error(f"記事の自動生成中にエラーが発生しました: {str(e)}")
                messages.warning(self.request, "記事の自動生成に失敗しました。")
        
        # トップページのキャッシュをクリア
        today = timezone.now().date()
        cache_key = f'index_view_data_{today}'
        cache.delete(cache_key)
        logger.info(f"Cleared index page cache: {cache_key}")
        
        return response

    def get_success_url(self):
        return reverse_lazy('event:detail', kwargs={'pk': self.object.pk})


class EventDetailUpdateView(LoginRequiredMixin, UpdateView):
    model = EventDetail
    form_class = EventDetailForm
    template_name = 'event/detail_form.html'

    def form_valid(self, form):
        response = super().form_valid(form)
        
        # PDFまたは動画がセットされていて、タイトルが空の場合は自動生成
        if (form.instance.slide_file or form.instance.youtube_url) and not form.instance.meta_description:
            try:
                blog_output = generate_blog(form.instance, model=GEMINI_MODEL)
                form.instance.h1 = blog_output.title
                form.instance.contents = blog_output.text
                form.instance.meta_description = blog_output.meta_description
                form.instance.save()
                messages.success(self.request, "記事を自動生成しました。")
                logger.info(f"記事を自動生成しました: {form.instance.id}")
            except Exception as e:
                logger.error(f"記事の自動生成中にエラーが発生しました: {str(e)}")
                messages.warning(self.request, "記事の自動生成に失敗しました。")
        
        return response

    def get_success_url(self):
        return reverse_lazy('event:detail', kwargs={'pk': self.object.pk})


    def is_valid_request(self, request, pk):
        pass


class GenerateBlogView(LoginRequiredMixin, View):
    def post(self, request, pk):
        try:
            event_detail = EventDetail.objects.get(id=pk)

            if event_detail.event.community.custom_user != request.user:
                messages.error(request, "Invalid request. You don't have permission to perform this action.")
                return redirect('event:detail', pk=event_detail.id)

            # BlogOutputモデルを受け取る
            blog_output = generate_blog(event_detail, model=GEMINI_MODEL)

            # BlogOutputの各フィールドを保存
            event_detail.h1 = blog_output.title
            event_detail.contents = blog_output.text
            event_detail.meta_description = blog_output.meta_description
            event_detail.save()

            logger.info(f"ブログ記事が生成されました。: {event_detail.id}")
            logger.info(f"ブログ記事のメタディスクリプション: {event_detail.meta_description}")

            messages.success(request, "ブログ記事が生成されました。")
            return redirect('event:detail', pk=event_detail.id)

        except Exception as e:
            logger.error(f"ブログ記事の生成中にエラーが発生しました: {str(e)}")
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
    paginate_by = 20

    def get_queryset(self):
        return Event.objects.filter(
            community__custom_user=self.request.user
        ).select_related('community').order_by('-date', '-start_time')

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
        
        # コミュニティ情報を取得
        context['community'] = Community.objects.filter(custom_user=self.request.user).first()
        
        # イベントにカレンダーURLを設定
        events = context['events']
        self.set_vrc_event_calendar_post_url(events)
        
        # イベントIDのリストを取得（ページネーション後のイベントのみ）
        event_ids = [event.id for event in events]
        
        if event_ids:
            # イベント詳細を一括取得
            event_details = EventDetail.objects.filter(
                event_id__in=event_ids
            ).select_related('event').order_by('created_at')
            
            # イベント詳細をイベントIDごとに整理
            event_detail_dict = {}
            for detail in event_details:
                if detail.event_id not in event_detail_dict:
                    event_detail_dict[detail.event_id] = []
                event_detail_dict[detail.event_id].append(detail)
            
            # 各イベントに詳細リストを設定
            for event in events:
                event.detail_list = event_detail_dict.get(event.id, [])
        else:
            # イベントが存在しない場合は空のリストを設定
            for event in events:
                event.detail_list = []

        # 現在のGETパラメータを取得（ページネーション用）
        query_params = self.request.GET.copy()
        if 'page' in query_params:
            del query_params['page']
        context['current_query_params'] = query_params.urlencode()

        return context


class EventDetailPastList(ListView):
    template_name = 'event/detail_history.html'
    model = EventDetail
    context_object_name = 'event_details'
    paginate_by = 20

    def get(self, request, *args, **kwargs):
        # 通常のget処理の前にページ番号をチェック
        page_str = request.GET.get('page', '1')

        try:
            # ページ番号のみを抽出（数字以外を除去）
            page = int(''.join(filter(str.isdigit, page_str)) or '1')
        except (ValueError, TypeError):
            # 無効なページ番号の場合は1ページ目にリダイレクト
            params = request.GET.copy()
            params['page'] = '1'
            return redirect(f"{request.path}?{params.urlencode()}")

        self.object_list = self.get_queryset()
        paginator = self.get_paginator(self.object_list, self.paginate_by)

        if page > paginator.num_pages and paginator.num_pages > 0:
            # 存在しないページ番号の場合は1ページ目にリダイレクト
            params = request.GET.copy()
            params['page'] = '1'
            return redirect(f"{request.path}?{params.urlencode()}")

        # ページ番号が有効な場合は通常の処理を続行
        request.GET = request.GET.copy()
        request.GET['page'] = str(page)
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

    def dispatch(self, request, *args, **kwargs):
        # コミュニティの承認状態をチェック
        community = Community.objects.filter(custom_user=request.user).first()
        if not community or community.status != 'approved':
            messages.error(request, '集会が承認されていないため、カレンダーにイベントを登録できません。')
            return redirect('event:my_list')
        return super().dispatch(request, *args, **kwargs)

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

            start_date = form.cleaned_data['start_date']
            start_time = form.cleaned_data['start_time']

            logger.info(f'イベント登録開始: コミュニティ={community.name}, 日付={start_date}, 開始時間={start_time}')

            # 同じ日時のイベントが存在するかチェック
            existing_event = Event.objects.filter(
                date=start_date,
                start_time=start_time,
                community=community
            ).first()

            if existing_event:
                logger.warning(
                    f'重複イベント検出: ID={existing_event.id}, コミュニティ={community.name}, 日付={start_date}, 開始時間={start_time}')
                messages.error(self.request, f'同じ日時（{start_date} {start_time}）にすでにイベントが登録されています。')
                return self.form_invalid(form)

            logger.info(f'Googleカレンダーにイベントを登録します: {community.name} Calendar ID={GOOGLE_CALENDAR_ID}')
            calendar_service = GoogleCalendarService(
                calendar_id=GOOGLE_CALENDAR_ID,
                credentials_path=GOOGLE_CALENDAR_CREDENTIALS if DEBUG else None
            )

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
                    week_number = int(form.cleaned_data['week_number'])
                    weekday = form.cleaned_data['weekday']
                    recurrence = [calendar_service._create_monthly_by_week_rrule(week_number, weekday)]
                logger.info(f'繰り返しルール設定: type={recurrence_type}, rule={recurrence}')

            # Googleカレンダーにイベントを作成
            event = calendar_service.create_event(
                summary=community.name,
                start_time=start_datetime,
                end_time=end_datetime,
                recurrence=recurrence
            )

            if event:
                logger.info(f'Googleカレンダーイベント作成成功: ID={event["id"]}, コミュニティ={community.name}')
                # イベントの同期を実行
                try:
                    # 内部的にGETリクエストを作成
                    from django.http import HttpRequest
                    sync_request = HttpRequest()
                    sync_request.method = 'GET'
                    sync_request.META = self.request.META
                    sync_request.headers = {'Request-Token': REQUEST_TOKEN}

                    response = sync_calendar_events(sync_request)
                    if response.status_code == 200:
                        messages.success(self.request, 'イベントが正常に登録されました')
                    else:
                        messages.warning(self.request, 'イベントは登録されましたが、同期に失敗しました')
                except Exception as e:
                    logger.error(f'イベント同期中にエラーが発生しました: {str(e)}')
                    messages.warning(self.request, 'イベントは登録されましたが、同期に失敗しました')

            return super().form_valid(form)

        except Exception as e:
            messages.error(self.request, f'イベントの登録に失敗しました: {str(e)}')
            return self.form_invalid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['community'] = Community.objects.filter(custom_user=self.request.user).first()
        return context
