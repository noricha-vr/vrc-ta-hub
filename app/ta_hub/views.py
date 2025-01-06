from django.views.generic import TemplateView, ListView, DetailView
from django.views.static import serve
from django.utils import timezone
from event.models import Event, EventDetail
from django.core.cache import cache
import logging

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
        
        # データをキャッシュに保存（1時間）
        cache_data = {
            'upcoming_events': upcoming_events,
            'upcoming_event_details': upcoming_event_details,
        }
        cache.set(cache_key, cache_data, 60 * 60)  # 60分 * 60秒
        
        context.update(cache_data)
        logger.info(f"Cache miss for {cache_key}")
        return context


def favicon_view(request):
    return serve(request, 'favicon.ico', document_root='site')


def apple_touch_icon_view(request):
    return serve(request, 'apple-touch-icon.png', document_root='site')
