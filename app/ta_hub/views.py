import logging

from django.core.cache import cache
from django.utils import timezone
from django.views.generic import TemplateView
from django.views.static import serve

from event.models import Event, EventDetail
from event.views import EventListView
from event_calendar.calendar_utils import generate_google_calendar_url

logger = logging.getLogger(__name__)


class IndexView(TemplateView):
    template_name = 'ta_hub/index.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # キャッシュキーの生成（日付ベース）
        today = timezone.now().date()
        cache_key = f'index_view_data_{today}'

        # Vketコラボ告知の表示判定
        current_datetime = timezone.now()
        vket_start_datetime = timezone.datetime(2025, 7, 12, 0, 0, tzinfo=timezone.get_current_timezone())
        vket_end_datetime = timezone.datetime(2025, 7, 28, 0, 0, tzinfo=timezone.get_current_timezone())
        context['show_vket_notice'] = current_datetime < vket_end_datetime
        context['vket_start_date'] = vket_start_datetime.date()
        context['vket_end_date'] = vket_end_datetime.date()
        logger.info(f"Vket notice visibility: {context['show_vket_notice']}")
        # キャッシュからデータを取得
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            context.update(cached_data)
            return context

        # キャッシュがない場合はデータベースから取得
        end_date = today + timezone.timedelta(days=7)
        upcoming_events = Event.objects.filter(
            date__gte=today,
            date__lte=end_date,
            # ポスター画像があるコミュニティのイベントのみ
            community__poster_image__isnull=False
        ).exclude(
            community__poster_image=''
        ).select_related('community').order_by('date', 'start_time')

        upcoming_event_details = EventDetail.objects.filter(
            event__date__gte=today,
            detail_type='LT',  # LTのみ
            # ポスター画像があるコミュニティのイベントのみ
            event__community__poster_image__isnull=False
        ).exclude(
            event__community__poster_image=''
        ).select_related('event', 'event__community').order_by('event__date', 'start_time')

        # 特別企画を取得（今日からイベント終了日の24時まで表示）
        special_events = EventDetail.objects.filter(
            detail_type='SPECIAL',
            event__date__gte=today,  # 今日以降のイベント
            # ポスター画像があるコミュニティのイベントのみ
            event__community__poster_image__isnull=False
        ).exclude(
            event__community__poster_image=''
        ).select_related('event', 'event__community').order_by('-event__date', '-start_time')[:10]

        # Google Calendar URLを生成
        event_list_view = EventListView()
        event_list_view.request = self.request

        # イベントとイベント詳細のGoogle Calendar URLを生成
        events_with_urls = []
        for event in upcoming_events:
            event_dict = {
                'id': event.id,
                'date': event.date,
                'start_time': event.start_time,
                'end_time': event.end_time,
                'community': event.community,
                'google_calendar_url': generate_google_calendar_url(self.request, event),
                'weekday': event.weekday,
            }
            events_with_urls.append(event_dict)

        details_with_urls = []
        for detail in upcoming_event_details:
            detail_dict = {
                'id': detail.id,
                'event': {
                    'id': detail.event.id,
                    'date': detail.event.date,
                    'start_time': detail.event.start_time,
                    'end_time': detail.event.end_time,
                    'community': detail.event.community,
                    'google_calendar_url': generate_google_calendar_url(self.request, detail.event),
                },
                'start_time': detail.start_time,
                'end_time': detail.end_time,
                'speaker': detail.speaker,
                'theme': detail.theme,
            }
            details_with_urls.append(detail_dict)

        # 特別企画の情報を整形
        special_events_data = []
        for special in special_events:
            special_dict = {
                'id': special.id,
                'pk': special.pk,  # テンプレートでpkを使用しているため追加
                'event': {
                    'id': special.event.id,
                    'date': special.event.date,
                    'start_time': special.event.start_time,
                    'end_time': special.event.end_time,
                    'community': special.event.community,
                },
                'h1': special.h1,
                'theme': special.theme,
                'meta_description': special.meta_description,
                'contents': special.contents,  # 記事本文を追加
            }
            special_events_data.append(special_dict)

        # データをキャッシュに保存（1時間）
        cache_data = {
            'upcoming_events': events_with_urls,
            'upcoming_event_details': details_with_urls,
            'special_events': special_events_data,
        }
        cache.set(cache_key, cache_data, 60 * 60)  # 60分 * 60秒

        context.update(cache_data)
        logger.info(f"Cache miss for {cache_key}")
        return context


def favicon_view(request):
    return serve(request, 'favicon.ico', document_root='site')


def apple_touch_icon_view(request):
    return serve(request, 'apple-touch-icon.png', document_root='site')
