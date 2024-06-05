from django.contrib import messages
from django.shortcuts import redirect
from django.views.generic import TemplateView, ListView, DetailView
import logging
from community.models import Community
from .models import Event
from django.http import HttpResponse
from icalendar import Calendar
import requests
from datetime import datetime

logger = logging.getLogger(__name__)


class EventListView(ListView):
    model = Event
    template_name = 'event/list.html'
    context_object_name = 'events'
    paginate_by = 20

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = queryset.filter().select_related('community').order_by('date', 'start_time')
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return context


class EventDetailView(DetailView):
    model = Event
    template_name = 'event/detail.html'
    context_object_name = 'event'


def import_events(request):
    # 公開カレンダーのURLを指定
    url = "https://calendar.google.com/calendar/ical/fbd1334d10a177831a23dfd723199ab4d02036ae31cbc04d6fc33f08ad93a3e7%40group.calendar.google.com/public/basic.ics"

    # URLからiCalendarデータを取得
    response = requests.get(url)

    # iCalendarデータをパース
    cal = Calendar.from_ical(response.text)

    # イベント情報を取得して保存
    for component in cal.walk():
        if component.name == "VEVENT":
            start = component.get("dtstart").dt
            end = component.get("dtend").dt

            # summaryとdescriptionをUTF-8でデコード
            summary = str(component.get("summary"))
            logger.info('summary:' + summary)
            # コミュニティーを名前と主催者名で検索
            community = Community.objects.filter(name=summary).first()

            if community:
                # 同じ日時の同じコミュニティーのイベントが存在するかチェック
                event_exists = Event.objects.filter(community=community, date=start.date(),
                                                    start_time=start.time()).exists()

                if not event_exists:
                    # イベントを作成
                    event = Event(
                        community=community,
                        date=start.date(),
                        start_time=start.time(),
                        duration=(end - start).total_seconds() // 60,  # 分単位に変換
                        weekday=start.strftime("%a"),
                    )
                    event.save()
    messages.info(request, f"Events imported successfully. {Event.objects.count()} events imported.")
    return redirect('event:list')
