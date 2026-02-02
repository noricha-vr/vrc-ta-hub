import datetime
import logging
import re
from datetime import datetime, timedelta, date
from typing import List, Dict
import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.db.models import QuerySet
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.urls import reverse_lazy, reverse
from django.utils import timezone
from django.views.generic import CreateView, UpdateView, DetailView, ListView, FormView
from django.views.generic import View
from django.views.generic.edit import DeleteView
from google.auth import default
from google.cloud import bigquery

from community.models import Community, WEEKDAY_CHOICES
from event.forms import EventDetailForm, EventSearchForm, GoogleCalendarEventForm, LTApplicationForm, LTApplicationReviewForm
from event.libs import convert_markdown, generate_blog
from event.models import EventDetail, Event
from event_calendar.calendar_utils import create_calendar_entry_url, generate_google_calendar_url
from url_filters import get_filtered_url
from utils.vrchat_time import get_vrchat_today
from website.settings import DEBUG, GOOGLE_CALENDAR_CREDENTIALS, GOOGLE_CALENDAR_ID, REQUEST_TOKEN, \
    GEMINI_MODEL
from .google_calendar import GoogleCalendarService
from .sync_to_google import DatabaseToGoogleSync

logger = logging.getLogger(__name__)

# BigQueryクライアントの遅延初期化
# CI環境でモジュールインポート時にGCP認証エラーが発生するのを防ぐ
_bigquery_client = None
_bigquery_project = None


def _get_bigquery_client():
    """BigQueryクライアントを遅延初期化して返す。

    GCP認証情報が必要になるまで初期化を遅延させることで、
    CI環境などGCP認証情報がない環境でもモジュールをインポートできる。
    """
    global _bigquery_client, _bigquery_project
    if _bigquery_client is None:
        import os
        if os.environ.get('TESTING'):
            from unittest.mock import MagicMock
            _bigquery_project = 'test-project'
            _bigquery_client = MagicMock()
        else:
            credentials, project = default()
            _bigquery_project = project
            _bigquery_client = bigquery.Client(
                credentials=credentials,
                project=project,
                location="asia-northeast1"
            )
    return _bigquery_client, _bigquery_project


class EventDeleteView(LoginRequiredMixin, DeleteView):
    model = Event
    success_url = reverse_lazy('event:my_list')

    def post(self, request, *args, **kwargs):
        event = self.get_object()

        # イベントが属する集会に対する削除権限をチェック（主催者のみ）
        if not event.community.can_delete(request.user):
            messages.error(request, "このイベントを削除する権限がありません。")
            return redirect('event:my_list')

        # 削除対象のコミュニティを取得
        user_community = event.community

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
                        logger.info(
                            f"Googleカレンダーからの削除を試行: Event ID={event_to_delete.google_calendar_event_id}")
                        calendar_service.delete_event(event_to_delete.google_calendar_event_id)
                        logger.info(
                            f"Googleカレンダーからの削除成功: Event ID={event_to_delete.google_calendar_event_id}")
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
        # VRChatterの生活リズムに合わせて朝4時を日付の境界とする
        today = get_vrchat_today()
        queryset = queryset.filter(date__gte=today).select_related('community').prefetch_related(
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

        # ページネーションリンク用に既存の 'page' パラメータを削除
        query_params_for_pagination = current_params.copy()
        if 'page' in query_params_for_pagination:
            del query_params_for_pagination['page']
        context['current_query_params'] = query_params_for_pagination.urlencode()

        context['weekday_urls'] = {
            choice[0]: get_filtered_url(base_url, current_params, 'weekday', choice[0])
            for choice in context['form'].fields['weekday'].choices
        }
        context['tag_urls'] = {
            choice[0]: get_filtered_url(base_url, current_params, 'tags', choice[0])
            for choice in context['form'].fields['tags'].choices
        }
        
        # GoogleカレンダーIDを追加
        context['google_calendar_id'] = GOOGLE_CALENDAR_ID

        return context


def extract_video_id(youtube_url):
    """
    YouTube URLからvideo_idを抽出する関数。

    Note:
        タイムスタンプも取得したい場合は extract_video_info() を使用してください。
    """
    if not youtube_url:
        return None
    pattern = r'(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/(?:[^\/\n\s]+\/\S+\/|(?:v|e(?:mbed)?)\/|\S*?[?&]v=)|youtu\.be\/)([a-zA-Z0-9_-]{11})'
    match = re.search(pattern, youtube_url)
    if match:
        return match.group(1)
    return None


def extract_video_info(youtube_url):
    """
    YouTube URLからvideo_idとタイムスタンプ（秒）を抽出する関数。

    Args:
        youtube_url: YouTube URL（例: https://www.youtube.com/watch?v=xxx?t=123）

    Returns:
        tuple: (video_id, start_time)
            - video_id: YouTube動画のID（11文字）またはNone
            - start_time: 開始秒数（整数）またはNone

    Examples:
        - ?t=123 → 123秒
        - &t=123 → 123秒
        - ?t=1m30s → 90秒
        - タイムスタンプなし → None
    """
    if not youtube_url:
        return None, None

    # video_idを抽出
    video_id = extract_video_id(youtube_url)

    # タイムスタンプを抽出（?t= または &t= パターン）
    start_time = None
    # パターン: 純粋な数字、または分秒形式（1m30s, 2m, 90s など）
    time_pattern = r'[?&]t=(\d+(?:m(?:\d+s)?|s)?)'
    time_match = re.search(time_pattern, youtube_url)

    if time_match:
        time_str = time_match.group(1)
        start_time = _parse_youtube_time(time_str)

    return video_id, start_time


def _parse_youtube_time(time_str):
    """
    YouTubeのタイムスタンプ形式を秒に変換する。

    Args:
        time_str: タイムスタンプ文字列（例: "123", "1m30s", "90s"）

    Returns:
        int: 秒数

    Examples:
        - "123" → 123
        - "1m30s" → 90
        - "90s" → 90
        - "2m" → 120
    """
    # 純粋な数値の場合
    if time_str.isdigit():
        return int(time_str)

    # 分秒形式（例: 1m30s, 2m, 90s）
    total_seconds = 0

    # 分を抽出
    minutes_match = re.search(r'(\d+)m', time_str)
    if minutes_match:
        minutes_to_seconds = 60
        total_seconds += int(minutes_match.group(1)) * minutes_to_seconds

    # 秒を抽出
    seconds_match = re.search(r'(\d+)s', time_str)
    if seconds_match:
        total_seconds += int(seconds_match.group(1))

    return total_seconds if total_seconds > 0 else None


class EventDetailView(DetailView):
    model = EventDetail
    template_name = 'event/detail.html'
    context_object_name = 'event_detail'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        event_detail = self.get_object()
        video_id, start_time = extract_video_info(event_detail.youtube_url)
        context['video_id'] = video_id
        context['start_time'] = start_time
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

        # Twitterボタン表示用のフラグとテンプレートを追加
        today = get_vrchat_today()
        twitter_display_until = event_detail.event.date + timedelta(days=7)
        context['twitter_button_active'] = today <= twitter_display_until
        context['twitter_templates'] = event_detail.event.community.twitter_template.all()

        # ユーザーがログインしていて、このイベントの集会管理者であるか確認
        if self.request.user.is_authenticated and event_detail.event.community.can_edit(self.request.user):
            context['is_community_owner'] = True
        else:
            context['is_community_owner'] = False

        # 構造化データ（BlogPosting）を生成（ブログ記事のみ）
        try:
            if event_detail.detail_type == 'BLOG':
                request = self.request
                absolute_url = request.build_absolute_uri()

                images: List[str] = []
                # コミュニティのポスター画像
                try:
                    poster = event_detail.event.community.poster_image
                    if poster and getattr(poster, 'url', None):
                        images.append(request.build_absolute_uri(poster.url))
                except Exception:
                    pass

                # YouTubeサムネイル
                if context.get('video_id'):
                    images.append(f"https://img.youtube.com/vi/{context['video_id']}/hqdefault.jpg")

                # 著者情報（発表者がいればPerson、いなければコミュニティ名のOrganization）
                if event_detail.speaker:
                    author_obj: Dict = {"@type": "Person", "name": event_detail.speaker}
                else:
                    author_obj = {"@type": "Organization", "name": event_detail.event.community.name}

                # パブリッシャ（コミュニティ）
                publisher_obj: Dict = {
                    "@type": "Organization",
                    "name": event_detail.event.community.name,
                }
                if images:
                    # ロゴとして最初の画像を使用（適切なロゴがない場合のフォールバック）
                    publisher_obj["logo"] = {"@type": "ImageObject", "url": images[0]}

                # メタディスクリプションのフォールバック
                description = (event_detail.meta_description or event_detail.theme or event_detail.title or "").strip()

                structured_data: Dict = {
                    "@context": "https://schema.org",
                    "@type": "BlogPosting",
                    "mainEntityOfPage": {"@type": "WebPage", "@id": absolute_url},
                    "headline": event_detail.title or "",
                    "url": absolute_url,
                    "inLanguage": "ja-JP",
                    "isAccessibleForFree": True,
                    "datePublished": event_detail.created_at.isoformat(),
                    "dateModified": event_detail.updated_at.isoformat(),
                    "description": description,
                    "publisher": publisher_obj,
                    "author": author_obj,
                }

                if images:
                    structured_data["image"] = images

                # 可能なら本文も追加（長すぎる場合はカット）
                if event_detail.contents:
                    body_text = event_detail.contents
                    if len(body_text) > 10000:
                        body_text = body_text[:10000]
                    structured_data["articleBody"] = body_text

                context['structured_data_json'] = json.dumps(structured_data, ensure_ascii=False)
                logger.info(f"Structured data prepared for EventDetail(BLOG): id={event_detail.id}")
        except Exception as e:
            logger.warning(f"Failed to prepare structured data for EventDetail id={event_detail.id}: {str(e)}")

        return context

    def _fetch_related_event_details(self, event_detail: EventDetail) -> List[EventDetail]:
        # キャッシュキーを生成
        cache_key = f'related_event_details_{event_detail.event_id}'
        related_event_details = cache.get(cache_key)
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
    """データベースからGoogleカレンダーへの同期（重複防止機能付き）"""
    if request.method != 'GET':
        return HttpResponse("Invalid request method.", status=405)

    # Get the Request-Token
    request_token = request.headers.get('Request-Token', '')
    
    # セキュリティチェック（必要に応じて）
    if request_token != REQUEST_TOKEN:
        return HttpResponse("Unauthorized", status=401)

    try:
        logger.info('=' * 80)
        logger.info('データベースからGoogleカレンダーへの同期開始')
        logger.info(f'同期開始時刻: {timezone.now()}')
        logger.info('=' * 80)
        
        # 重複防止機能付きの同期処理を実行
        sync = DatabaseToGoogleSync()
        stats = sync.sync_all_communities(months_ahead=3)
        
        # 同期結果のサマリー
        logger.info('=' * 80)
        logger.info('同期完了サマリー:')
        logger.info(f"  現在のDBイベント総数: {Event.objects.count()}件")
        logger.info(f"  新規作成: {stats['created']}件")
        logger.info(f"  更新: {stats['updated']}件")
        logger.info(f"  削除: {stats['deleted']}件")
        logger.info(f"  エラー: {stats['errors']}件")
        
        # 重複防止が機能した場合のログ
        if stats.get('duplicate_prevented', 0) > 0:
            logger.info(f"  重複防止により更新に切り替え: {stats['duplicate_prevented']}件")
        
        logger.info(f'同期終了時刻: {timezone.now()}')
        logger.info('=' * 80)
        
        # レスポンスメッセージ
        response_message = (
            f"Calendar events synchronized successfully. "
            f"Created: {stats['created']}, Updated: {stats['updated']}, "
            f"Skipped: {stats.get('skipped', 0)}, "
            f"Deleted: {stats['deleted']}, Errors: {stats['errors']}"
        )
        
        if stats.get('duplicate_prevented', 0) > 0:
            response_message += f", Duplicate prevented: {stats['duplicate_prevented']}"
        
        return HttpResponse(response_message, status=200)

    except Exception as e:
        logger.error('=' * 80)
        logger.error(f"同期失敗: {str(e)}")
        logger.error('=' * 80, exc_info=True)
        return HttpResponse(f"Failed to sync calendar events: {str(e)}", status=500)


def delete_outdated_events(calendar_events: List[Dict], today: date) -> None:
    """
    DBに登録されているイベントのうち、Googleカレンダーに存在しないものを削除する

    このメソッドが必要な理由:
    1. データの整合性維持: GoogleカレンダーとDBのイベントデータを
       同期させ、システム全体でのデータの一貫性を保ちます。
    2. 不要データの削除: キャンセルされたイベントや終了したイベントを
       適切に削除し、DBの肥大化を防ぎます。
    3. ユーザー体験の向上: 過去のイベントや無効なイベントを表示から
       除外することで、ユーザーに適切な情報のみを提供します。

    Args:
        calendar_events (List[Dict]): Googleカレンダーから取得したイベントリスト
        today (date): 現在の日付
    """
    future_events = Event.objects.filter(date__gte=today).values(
        'id', 'community__name', 'date', 'start_time'
    )

    for db_event in future_events:
        # アウェアなdatetimeオブジェクトを作成
        db_event_naive = datetime.combine(
            db_event['date'], db_event['start_time']
        )
        db_event_datetime = timezone.make_aware(db_event_naive, timezone.get_current_timezone())
        db_event_str = f"{db_event_datetime.isoformat()} {db_event['community__name']}"

        # カレンダーイベントとの一致をチェック
        found = False
        for e in calendar_events:
            # カレンダーイベントの開始時間をパースする
            calendar_start_str = e['start'].get('dateTime', e['start'].get('date'))
            try:
                calendar_start = datetime.strptime(calendar_start_str, '%Y-%m-%dT%H:%M:%S%z')
                calendar_start_local = calendar_start.astimezone(timezone.get_current_timezone())

                # 日付と時間を比較（時間は時と分だけを比較）
                same_date = calendar_start_local.date() == db_event['date']
                same_time = (calendar_start_local.hour == db_event['start_time'].hour and
                             calendar_start_local.minute == db_event['start_time'].minute)
                same_name = e['summary'].strip() == db_event['community__name']

                if same_date and same_time and same_name:
                    found = True
                    logger.info(
                        f"イベント一致確認: DB={db_event_str}, Calendar={calendar_start_local.isoformat()} {e['summary']}")
                    break
            except Exception as parsing_err:
                logger.warning(f"カレンダーイベント解析エラー: {str(parsing_err)} - {calendar_start_str}")
                continue

        if not found:
            logger.warning(f"削除対象イベント: {db_event_str} - カレンダーに存在しないため削除します")
            Event.objects.filter(id=db_event['id']).delete()
            logger.info(f"Event deleted: {db_event_str}")
        else:
            logger.info(f"イベント保持: {db_event_str} - カレンダーに存在するため保持します")


def register_calendar_events(calendar_events: List[Dict]) -> None:
    """
    Googleカレンダーのイベントをデータベースに登録する

    このメソッドが必要な理由:
    1. イベント情報の集中管理: Googleカレンダーのイベント情報を
       システムのDBに取り込み、一元管理を実現します。
    2. イベント情報の同期: 新規イベントや更新されたイベント情報を
       システムに反映し、最新の情報を維持します。
    3. コミュニティ活動の可視化: VRChatコミュニティの活動を
       システム上で可視化し、ユーザーの参加を促進します。

    Args:
        calendar_events (List[Dict]): Googleカレンダーから取得したイベントリスト
    """
    for event in calendar_events:
        start_datetime = event['start'].get('dateTime', event['start'].get('date'))
        end_datetime = event['end'].get('dateTime', event['end'].get('date'))
        summary = event['summary'].strip()

        # タイムゾーン付きの日時を解析
        start = datetime.strptime(start_datetime, '%Y-%m-%dT%H:%M:%S%z')
        end = datetime.strptime(end_datetime, '%Y-%m-%dT%H:%M:%S%z')

        # 現地タイムゾーン（Asia/Tokyo）に変換
        current_tz = timezone.get_current_timezone()
        start_local = start.astimezone(current_tz)
        end_local = end.astimezone(current_tz)

        community = Community.objects.filter(name=summary).first()
        if not community:
            logger.warning(f"Community not found: {summary}")
            continue

        event_str = f"{start_local} - {end_local} {summary}"
        logger.info(f"Event: {event_str}")

        # ローカル時間でデータベースを検索
        existing_event = Event.objects.filter(
            community=community,
            date=start_local.date(),
            start_time=start_local.time()
        ).first()

        if existing_event:
            if (existing_event.duration != (end_local - start_local).total_seconds() // 60 or
                    existing_event.google_calendar_event_id != event['id']):
                existing_event.duration = (end_local - start_local).total_seconds() // 60
                existing_event.google_calendar_event_id = event['id']
                existing_event.save()

                # 更新されたイベントのキャッシュをクリア
                cache_key = f'calendar_entry_url_{existing_event.id}'
                cache.delete(cache_key)

                logger.info(f"Event updated: {event_str}")
        else:
            new_event = Event.objects.create(
                community=community,
                date=start_local.date(),
                start_time=start_local.time(),
                duration=(end_local - start_local).total_seconds() // 60,
                weekday=start_local.strftime("%a"),
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
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event'] = self.event
        # イベントが開催前かどうかを判定
        from datetime import date
        context['is_before_event'] = self.event.date > date.today()
        return context

    def form_valid(self, form):
        form.instance.event = self.event
        response = super().form_valid(form)

        # チェックボックスがONで、LTタイプで、PDFまたは動画がセットされている場合は自動生成
        generate_blog = form.cleaned_data.get('generate_blog_article', False)
        if (generate_blog and 
            form.instance.detail_type == 'LT' and 
            (form.instance.slide_file or form.instance.youtube_url)):
            try:
                from event.libs import generate_blog as generate_blog_func
                blog_output = generate_blog_func(form.instance, model=GEMINI_MODEL)
                # 空でないことを確認
                if blog_output.title:
                    form.instance.h1 = blog_output.title
                    form.instance.contents = blog_output.text
                    form.instance.meta_description = blog_output.meta_description
                    form.instance.save()
                    messages.success(self.request, "記事を自動生成しました。")
                    logger.info(f"記事を自動生成しました: {form.instance.id}")
                else:
                    logger.warning(f"記事の自動生成に失敗しました（空の結果）: {form.instance.id}")
                    messages.warning(self.request, "記事の自動生成に失敗しました。")
            except Exception as e:
                logger.error(f"記事の自動生成中にエラーが発生しました: {str(e)}")
                messages.error(self.request, f"記事の自動生成中にエラーが発生しました: {str(e)}")

        return response

    def get_success_url(self):
        return reverse_lazy('event:detail', kwargs={'pk': self.object.pk})


class EventDetailUpdateView(LoginRequiredMixin, UpdateView):
    model = EventDetail
    form_class = EventDetailForm
    template_name = 'event/detail_form.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event'] = self.object.event
        # イベントが開催前かどうかを判定
        from datetime import date
        context['is_before_event'] = self.object.event.date > date.today()
        return context

    def form_valid(self, form):
        response = super().form_valid(form)

        # チェックボックスがONで、LTタイプで、PDFまたは動画がセットされている場合は自動生成
        generate_blog_flag = form.cleaned_data.get('generate_blog_article', False)
        if (generate_blog_flag and 
            form.instance.detail_type == 'LT' and 
            (form.instance.slide_file or form.instance.youtube_url)):
            try:
                blog_output = generate_blog(form.instance, model=GEMINI_MODEL)
                # 空でないことを確認
                if blog_output.title:
                    form.instance.h1 = blog_output.title
                    form.instance.contents = blog_output.text
                    form.instance.meta_description = blog_output.meta_description
                    form.instance.save()
                    messages.success(self.request, "記事を自動生成しました。")
                    logger.info(f"記事を自動生成しました: {form.instance.id}")
                else:
                    logger.warning(f"記事の自動生成に失敗しました（空の結果）: {form.instance.id}")
                    messages.warning(self.request, "記事の自動生成に失敗しました。")
            except Exception as e:
                logger.error(f"記事の自動生成中にエラーが発生しました: {str(e)}")
                messages.error(self.request, f"記事の自動生成中にエラーが発生しました: {str(e)}")

        return response

    def get_success_url(self):
        return reverse_lazy('event:detail', kwargs={'pk': self.object.pk})

    def is_valid_request(self, request, pk):
        pass


class GenerateBlogView(LoginRequiredMixin, View):
    def post(self, request, pk):
        try:
            event_detail = EventDetail.objects.get(id=pk)

            if not event_detail.event.community.can_edit(request.user):
                messages.error(request, "Invalid request. You don't have permission to perform this action.")
                return redirect('event:detail', pk=event_detail.id)

            # LTタイプのみ記事生成を許可
            if event_detail.detail_type != 'LT':
                messages.error(request, "記事の自動生成はLT（発表）タイプのみ利用可能です。")
                return redirect('event:detail', pk=event_detail.id)

            # BlogOutputモデルを受け取る
            blog_output = generate_blog(event_detail, model=GEMINI_MODEL)

            # 空でないことを確認
            if blog_output.title:
                # BlogOutputの各フィールドを保存
                event_detail.h1 = blog_output.title
                event_detail.contents = blog_output.text
                event_detail.meta_description = blog_output.meta_description
                event_detail.save()

                logger.info(f"ブログ記事が生成されました。: {event_detail.id}")
                logger.info(f"ブログ記事のメタディスクリプション: {event_detail.meta_description}")

                messages.success(request, "ブログ記事が生成されました。")
            else:
                logger.warning(f"ブログ記事の生成に失敗しました（空の結果）: {event_detail.id}")
                messages.warning(request, "ブログ記事の生成に失敗しました。")
            
            return redirect('event:detail', pk=event_detail.id)

        except Exception as e:
            logger.error(f"ブログ記事の生成中にエラーが発生しました: {str(e)}")
            messages.error(request, f"エラーが発生しました: {str(e)}")
            return redirect('event:detail', pk=pk)

    def save_to_bigquery(self, pk, video_id, user_id, transcript, prompt, response):
        client, project = _get_bigquery_client()
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

    def _get_user_communities(self):
        """ユーザーが管理者である集会のID一覧を取得する"""
        return list(
            self.request.user.community_memberships.values_list('community_id', flat=True)
        )

    def _get_active_community(self):
        """アクティブな集会を取得する"""
        active_community_id = self.request.session.get('active_community_id')
        if active_community_id:
            membership = self.request.user.community_memberships.filter(
                community_id=active_community_id
            ).select_related('community').first()
            if membership:
                return membership.community

        # フォールバック: 最初の管理集会
        membership = self.request.user.community_memberships.select_related('community').first()
        if membership:
            return membership.community

        return None

    def _get_user_communities_list(self):
        """ユーザーが管理者である集会のオブジェクト一覧を取得する"""
        communities = []

        # メンバーシップベースの集会
        for membership in self.request.user.community_memberships.select_related('community'):
            communities.append(membership.community)

        return communities

    def _get_warnings(self, community):
        """アクティブな集会に対する警告リストを取得する"""
        warnings = []
        if not community:
            return warnings

        # ポスター未設定警告
        if not community.poster_image:
            warnings.append({
                'type': 'warning',
                'message': 'ポスター画像が設定されていません。ポスター画像を設定しないと、集会一覧やトップページにイベントが表示されません。',
                'link': reverse('community:update'),
                'link_text': '設定する'
            })

        # 今後のイベントなし警告
        future_events = Event.objects.filter(
            community=community,
            date__gte=timezone.now().date()
        ).exists()
        if not future_events:
            warnings.append({
                'type': 'info',
                'message': '今後のイベントが登録されていません。',
                'link': reverse('event:calendar_create'),
                'link_text': 'イベントを登録'
            })

        return warnings

    def get_queryset(self):
        today = get_vrchat_today()

        user_community_ids = self._get_user_communities()

        # アクティブな集会が設定されている場合はその集会のみを対象に
        active_community_id = self.request.session.get('active_community_id')
        if active_community_id and active_community_id in user_community_ids:
            community_ids = [active_community_id]
        else:
            # フォールバック: 全ての管理集会
            community_ids = user_community_ids

        # 未来のイベントを最大2つまで取得
        future_events = Event.objects.filter(
            community_id__in=community_ids,
            date__gte=today
        ).select_related('community').order_by('date', 'start_time')[:2]

        # 過去のイベントを取得
        past_events = Event.objects.filter(
            community_id__in=community_ids,
            date__lt=today
        ).select_related('community').order_by('-date', '-start_time')

        # 未来のイベントと過去のイベントを結合
        return list(future_events) + list(past_events)

    def set_vrc_event_calendar_post_url(self, queryset: QuerySet) -> QuerySet:
        """
        イベントのGoogleフォームのURLを設定する
        """
        for event in queryset:
            if get_vrchat_today() > event.date:
                continue
            event.calendar_url = create_calendar_entry_url(event)
        return queryset

    def _set_twitter_button_flags(self, events):
        """
        イベントごとにTwitterボタン表示フラグを設定する
        
        Args:
            events (list): イベントリスト
            
        Returns:
            list: Twitterボタン表示フラグが設定されたイベントリスト
        """
        today = get_vrchat_today()
        for event in events:
            # イベント日から1週間後の日付を計算
            twitter_display_until = event.date + timedelta(days=7)
            # イベント日から1週間以内ならTwitterボタンを表示
            event.twitter_button_active = today <= twitter_display_until
        return events

    def _attach_event_details(self, events):
        """
        イベントごとにイベント詳細情報を取得・設定する
        
        Args:
            events (list): イベントリスト
            
        Returns:
            list: イベント詳細が添付されたイベントリスト
        """
        # イベントIDのリストを取得
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

        return events

    def _prepare_pagination_params(self):
        """
        ページネーション用のGETパラメータを準備する
        
        Returns:
            str: エンコードされたクエリパラメータ
        """
        query_params = self.request.GET.copy()
        if 'page' in query_params:
            del query_params['page']
        return query_params.urlencode()

    def get_context_data(self, **kwargs):
        """
        テンプレートに渡すコンテキストデータを準備する

        各機能は専用のプライベートメソッドに分割され、
        このメソッドではそれらを順番に呼び出して結果を組み合わせる
        """
        context = super().get_context_data(**kwargs)

        # コミュニティ情報を取得（アクティブな集会）
        active_community = self._get_active_community()
        context['community'] = active_community
        context['active_community'] = active_community

        # 所属集会一覧を取得
        context['communities'] = self._get_user_communities_list()

        # 警告リストを取得
        context['warnings'] = self._get_warnings(active_community)

        # イベントリストを取得
        events = context['events']

        # イベントにカレンダーURLを設定
        events = self.set_vrc_event_calendar_post_url(events)

        # Twitterボタン表示用のフラグを設定
        events = self._set_twitter_button_flags(events)

        # イベント詳細情報を取得・設定
        events = self._attach_event_details(events)

        # 更新されたイベントリストをコンテキストに再設定
        context['events'] = events

        # ページネーション用のパラメータを設定
        context['current_query_params'] = self._prepare_pagination_params()

        # 未来のイベントが存在するかをチェック
        today = get_vrchat_today()
        future_events_exist = any(event.date >= today for event in events)
        context['has_future_events'] = future_events_exist

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
            detail_type='LT'  # LTのみ表示
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


class EventLogListView(ListView):
    """特別企画とブログの一覧表示"""
    template_name = 'event/event_log_list.html'
    model = EventDetail
    context_object_name = 'event_logs'
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
            detail_type__in=['SPECIAL', 'BLOG']  # 特別企画とブログのみ表示
        ).select_related('event', 'event__community').order_by('-event__date', '-start_time')

        community_name = self.request.GET.get('community_name', '').strip()
        if community_name:
            queryset = queryset.filter(event__community__name__icontains=community_name)

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

    def _get_active_community(self):
        """アクティブな集会を取得する"""
        active_community_id = self.request.session.get('active_community_id')
        if active_community_id:
            membership = self.request.user.community_memberships.filter(
                community_id=active_community_id
            ).select_related('community').first()
            if membership:
                return membership.community

        # フォールバック: 最初の管理集会
        membership = self.request.user.community_memberships.select_related('community').first()
        if membership:
            return membership.community

        return None

    def dispatch(self, request, *args, **kwargs):
        # コミュニティの承認状態をチェック
        community = self._get_active_community()
        if not community or community.status != 'approved':
            messages.error(request, '集会が承認されていないため、カレンダーにイベントを登録できません。')
            return redirect('event:my_list')
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # ログインユーザーのコミュニティを初期値として設定
        if self.request.user.is_authenticated:
            community = self._get_active_community()
            if community:
                kwargs['initial'] = {
                    'start_time': community.start_time,
                    'duration': community.duration
                }
        return kwargs

    def form_valid(self, form):
        try:
            # フォームのバリデーション後にコミュニティを取得
            community = self._get_active_community()
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

            # 開始時刻と終了時刻を設定
            start_datetime = datetime.combine(start_date, start_time)
            duration = form.cleaned_data['duration']

            # 新しいイベントをDBに保存
            try:
                new_event = Event.objects.create(
                    community=community,
                    date=start_date,
                    start_time=start_time,
                    duration=duration,
                    weekday=start_datetime.strftime("%a")
                    # google_calendar_event_idは同期時に設定される
                )
                logger.info(f'イベントをDBに登録: ID={new_event.id}, 日付={start_date}, 開始時間={start_time}')
                
                # バックグラウンドで同期処理を実行
                try:
                    # 内部的にGETリクエストを作成
                    from django.http import HttpRequest
                    sync_request = HttpRequest()
                    sync_request.method = 'GET'
                    sync_request.META = self.request.META
                    sync_request.headers = {'Request-Token': REQUEST_TOKEN}

                    # 同期処理を実行（エラーがあっても継続）
                    response = sync_calendar_events(sync_request)
                    if response.status_code != 200:
                        logger.warning(f'カレンダー同期で警告: ステータスコード={response.status_code}')
                except Exception as e:
                    logger.error(f'イベント同期中にエラーが発生しました: {str(e)}', exc_info=True)

                # イベントの作成が成功した場合、キャッシュをクリア
                cache_key = f'calendar_entry_url_{new_event.id}'
                cache.delete(cache_key)
                
                messages.success(self.request, 'イベントが正常に登録されました')
                
            except Exception as e:
                logger.error(f'イベントのDB登録でエラー: {str(e)}', exc_info=True)
                messages.error(self.request, 'イベントの登録に失敗しました')
                return self.form_invalid(form)

            return super().form_valid(form)

        except Exception as e:
            messages.error(self.request, f'イベントの登録に失敗しました: {str(e)}')
            return self.form_invalid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['community'] = self._get_active_community()
        return context


class LTApplicationCreateView(LoginRequiredMixin, FormView):
    """LT発表の申請ビュー"""
    template_name = 'event/lt_application_form.html'
    form_class = LTApplicationForm

    def dispatch(self, request, *args, **kwargs):
        self.community = get_object_or_404(Community, pk=kwargs['community_pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['community'] = self.community
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['community'] = self.community
        return context

    def form_valid(self, form):
        # EventDetailを作成
        event = form.cleaned_data['event']
        event_detail = EventDetail.objects.create(
            event=event,
            detail_type='LT',
            theme=form.cleaned_data['theme'],
            speaker=form.cleaned_data['speaker'],
            duration=form.cleaned_data['duration'],
            start_time=event.start_time,
            status='pending',
            applicant=self.request.user,
            additional_info=form.cleaned_data.get('additional_info', ''),
        )

        # 主催者に通知
        from event.notifications import notify_owners_of_new_application
        notify_owners_of_new_application(event_detail, request=self.request)

        messages.success(
            self.request,
            f'LT発表を申請しました。主催者の承認をお待ちください。'
        )
        logger.info(
            f'LT申請作成: Community={self.community.name}, Event={event.date}, '
            f'Theme={event_detail.theme}, User={self.request.user.user_name}'
        )

        return redirect('community:detail', pk=self.community.pk)


class LTApplicationReviewView(LoginRequiredMixin, FormView):
    """LT申請の承認/却下ビュー"""
    template_name = 'event/lt_application_review.html'
    form_class = LTApplicationReviewForm

    def dispatch(self, request, *args, **kwargs):
        # LoginRequiredMixinのチェックを先に実行
        # 未ログインの場合はログインページにリダイレクト
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        self.event_detail = get_object_or_404(EventDetail, pk=kwargs['pk'])
        self.community = self.event_detail.event.community

        # 権限チェック
        if not self.community.can_edit(request.user):
            messages.error(request, 'この申請を確認する権限がありません。')
            return redirect('community:detail', pk=self.community.pk)

        # 既に処理済みの場合
        if self.event_detail.status != 'pending':
            messages.info(request, 'この申請は既に処理されています。')
            return redirect('event:my_list')

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event_detail'] = self.event_detail
        context['community'] = self.community
        return context

    def form_valid(self, form):
        action = form.cleaned_data['action']

        if action == 'approve':
            self.event_detail.status = 'approved'
            status_text = '承認'
        else:
            self.event_detail.status = 'rejected'
            self.event_detail.rejection_reason = form.cleaned_data['rejection_reason']
            status_text = '却下'

        self.event_detail.save()

        # 申請者に通知
        from event.notifications import notify_applicant_of_result
        notify_applicant_of_result(self.event_detail, request=self.request)

        messages.success(self.request, f'申請を{status_text}しました。')
        logger.info(
            f'LT申請{status_text}: EventDetail ID={self.event_detail.pk}, '
            f'Community={self.community.name}, Reviewer={self.request.user.user_name}'
        )

        return redirect('event:my_list')


class LTApplicationApproveView(LoginRequiredMixin, View):
    """LT申請の承認ビュー（AJAX対応）"""

    def post(self, request, pk):
        event_detail = get_object_or_404(EventDetail, pk=pk)
        community = event_detail.event.community

        # 権限チェック
        if not community.can_edit(request.user):
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': '権限がありません。'}, status=403)
            messages.error(request, 'この申請を承認する権限がありません。')
            return redirect('event:my_list')

        # 既に処理済みの場合
        if event_detail.status != 'pending':
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': 'この申請は既に処理されています。'}, status=400)
            messages.info(request, 'この申請は既に処理されています。')
            return redirect('event:my_list')

        # 承認処理
        event_detail.status = 'approved'
        event_detail.save()

        # 申請者に通知
        from event.notifications import notify_applicant_of_result
        notify_applicant_of_result(event_detail, request=request)

        logger.info(
            f'LT申請承認: EventDetail ID={event_detail.pk}, '
            f'Community={community.name}, Reviewer={request.user.user_name}'
        )

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'status': 'approved'})

        messages.success(request, '申請を承認しました。')
        return redirect('event:my_list')


class LTApplicationRejectView(LoginRequiredMixin, View):
    """LT申請の却下ビュー（AJAX対応）"""

    def post(self, request, pk):
        event_detail = get_object_or_404(EventDetail, pk=pk)
        community = event_detail.event.community

        # 権限チェック
        if not community.can_edit(request.user):
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': '権限がありません。'}, status=403)
            messages.error(request, 'この申請を却下する権限がありません。')
            return redirect('event:my_list')

        # 既に処理済みの場合
        if event_detail.status != 'pending':
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': 'この申請は既に処理されています。'}, status=400)
            messages.info(request, 'この申請は既に処理されています。')
            return redirect('event:my_list')

        # 却下理由を取得
        rejection_reason = request.POST.get('rejection_reason', '').strip()
        if not rejection_reason:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': '却下理由を入力してください。'}, status=400)
            messages.error(request, '却下理由を入力してください。')
            return redirect('event:my_list')

        # 却下処理
        event_detail.status = 'rejected'
        event_detail.rejection_reason = rejection_reason
        event_detail.save()

        # 申請者に通知
        from event.notifications import notify_applicant_of_result
        notify_applicant_of_result(event_detail, request=request)

        logger.info(
            f'LT申請却下: EventDetail ID={event_detail.pk}, '
            f'Community={community.name}, Reviewer={request.user.user_name}, '
            f'Reason={rejection_reason}'
        )

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'status': 'rejected'})

        messages.success(request, '申請を却下しました。')
        return redirect('event:my_list')
