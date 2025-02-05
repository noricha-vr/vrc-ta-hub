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

        # キャッシュからデータを取得
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            context.update(cached_data)
            return context

        # キャッシュがない場合はデータベースから取得
        end_date = today + timezone.timedelta(days=7)
        upcoming_events = Event.objects.filter(
            date__gte=today,
            date__lte=end_date
        ).select_related('community').order_by('date', 'start_time')

        upcoming_event_details = EventDetail.objects.filter(
            event__date__gte=today
        ).select_related('event', 'event__community').order_by('event__date', 'start_time')

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

        # データをキャッシュに保存（1時間）
        cache_data = {
            'upcoming_events': events_with_urls,
            'upcoming_event_details': details_with_urls,
        }
        cache.set(cache_key, cache_data, 60 * 60)  # 60分 * 60秒

        context.update(cache_data)
        logger.info(f"Cache miss for {cache_key}")
        return context


def favicon_view(request):
    return serve(request, 'favicon.ico', document_root='site')


def apple_touch_icon_view(request):
    return serve(request, 'apple-touch-icon.png', document_root='site')
