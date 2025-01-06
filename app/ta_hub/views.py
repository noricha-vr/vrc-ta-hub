from django.views.generic import TemplateView, ListView, DetailView
from django.views.static import serve
from django.utils import timezone
from event.models import Event, EventDetail
import logging

logger = logging.getLogger(__name__)

class IndexView(TemplateView):
    template_name = 'ta_hub/index.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.now().date()
        # 向こう7日間のイベントを取得
        end_date = today + timezone.timedelta(days=7)
        context['upcoming_events'] = Event.objects.filter(
            date__gte=today,
            date__lte=end_date
        ).select_related('community').order_by('date', 'start_time')
        
        # 予約済みのイベント詳細を取得
        context['upcoming_event_details'] = EventDetail.objects.filter(
            event__date__gte=today
        ).select_related('event', 'event__community').order_by('event__date', 'start_time')
        
        logger.info(context['upcoming_events'])
        return context


def favicon_view(request):
    return serve(request, 'favicon.ico', document_root='site')


def apple_touch_icon_view(request):
    return serve(request, 'apple-touch-icon.png', document_root='site')
