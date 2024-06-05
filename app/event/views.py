import re
from django.contrib import messages
from django.shortcuts import redirect
from django.views.generic import TemplateView, ListView, DetailView
import logging
from community.models import Community
from .models import Event, EventDetail
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


def extract_video_id(youtube_url):
    """
    YouTube URLからvideo_idを抽出する関数。
    """
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
        return context


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
            summary = str(component.get("summary", "")).strip()
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
            else:
                messages.warning(request, f"Community not found: {summary}")
    messages.info(request, f"Events imported successfully. {Event.objects.count()} events imported.")
    return redirect('event:list')
