# app/community/views.py
import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.mixins import UserPassesTestMixin
from django.db import DataError
from django.db.models import Min, Q, F
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, DetailView
from django.views.generic import UpdateView

from event.models import Event
from url_filters import get_filtered_url
from .forms import CommunitySearchForm
from .forms import CommunityUpdateForm
from .libs import get_join_type
from .models import Community

logger = logging.getLogger(__name__)


# app/community/views.py

class CommunityListView(ListView):
    model = Community
    template_name = 'community/list.html'
    context_object_name = 'communities'
    paginate_by = 18

    def get_queryset(self):
        queryset = super().get_queryset()
        now = timezone.now()
        queryset = queryset.filter(is_accepted=True, end_at__isnull=True)

        # 最新のイベント日を取得
        queryset = queryset.annotate(
            latest_event_date=Min(
                'events__date',
                filter=Q(events__date__gte=now.date())
            )
        )

        form = CommunitySearchForm(self.request.GET)
        if form.is_valid():
            if query := form.cleaned_data['query']:
                queryset = queryset.filter(Q(name__icontains=query) | Q(description__icontains=query))
            if weekdays := form.cleaned_data['weekdays']:
                weekday_filters = Q()
                for weekday in weekdays:
                    weekday_filters |= Q(weekdays__contains=weekday)
                queryset = queryset.filter(weekday_filters)
            if tags := form.cleaned_data['tags']:
                # タグフィルタリングの修正
                tag_filters = Q()
                for tag in tags:
                    tag_filters |= Q(tags__contains=[tag])
                queryset = queryset.filter(tag_filters)

        # 最新のイベント日でソート（NULL値は最後に）
        queryset = queryset.order_by(
            F('latest_event_date').asc(nulls_last=True),
            '-updated_at'
        )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = CommunitySearchForm(self.request.GET or None)
        context['selected_weekdays'] = self.request.GET.getlist('weekdays')
        context['selected_tags'] = self.request.GET.getlist('tags')

        base_url = reverse('community:list')
        current_params = self.request.GET.copy()

        context['weekday_urls'] = {
            choice[0]: get_filtered_url(base_url, current_params, 'weekdays', choice[0])
            for choice in context['form'].fields['weekdays'].choices
        }
        context['tag_urls'] = {
            choice[0]: get_filtered_url(base_url, current_params, 'tags', choice[0])
            for choice in context['form'].fields['tags'].choices
        }

        # 曜日の選択肢をコンテキストに追加
        context['weekday_choices'] = dict(WEEKDAY_CHOICES)
        # タグの選択肢をコンテキストに追加
        context['tag_choices'] = dict(TAGS)
        # 検索結果の件数をコンテキストに追加
        context['search_count'] = self.get_queryset().count()
        return context


from .models import Community, WEEKDAY_CHOICES, TAGS


class CommunityDetailView(DetailView):
    model = Community
    template_name = 'community/detail.html'
    context_object_name = 'community'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        community = context['community']
        if community.twitter_hashtag:
            community.twitter_hashtags = [f'#{tag.strip()}' for tag in community.twitter_hashtag.split('#') if
                                          tag.strip()]
        community.join_type = get_join_type(community.organizer_url)

        now = timezone.now()

        # 予定されているイベント scheduled events
        scheduled_events = Event.objects.filter(
            community=community, date__gte=now).prefetch_related('details').order_by('date', 'start_time')[:4]
        context['scheduled_events'] = self.get_event_details(scheduled_events)

        # 過去のイベントを取得。ただし、event_detailが存在するもののみ
        past_events = Event.objects.filter(
            community=community, date__lt=now
        ).filter(
            Q(details__theme__isnull=False) | Q(details__theme__gt='')
        ).prefetch_related('details').order_by('-date', '-start_time')[:6]
        context['past_events'] = self.get_event_details(past_events)

        # 曜日の選択肢をコンテキストに追加
        context['weekday_choices'] = dict(WEEKDAY_CHOICES)

        # タグの選択肢をコンテキストに追加
        context['tag_choices'] = dict(TAGS)

        # 承認ボタンの表示
        if self.request.user != community.custom_user and not community.is_accepted:
            context['show_accept_button'] = True
        return context

    def get_event_details(self, events):
        event_details_list = []
        last_event = None
        for event in events:
            details = event.details.all()
            if event == last_event:
                continue
            event_details_list.append({
                'details': details,
                'event': event,
            })
            last_event = event
        return event_details_list


class CommunityUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Community
    form_class = CommunityUpdateForm
    template_name = 'community/update.html'
    success_url = reverse_lazy('account:settings')

    def test_func(self):
        community = self.get_object()
        return self.request.user == community.custom_user

    def form_valid(self, form):
        try:
            response = super().form_valid(form)
            calendar_entry = self.object.calendar_entry
            calendar_entry.join_condition = form.cleaned_data['join_condition']
            calendar_entry.event_detail = form.cleaned_data['event_detail']
            calendar_entry.how_to_join = form.cleaned_data['how_to_join']
            calendar_entry.note = form.cleaned_data['note']
            calendar_entry.is_overseas_user = form.cleaned_data['is_overseas_user']
            calendar_entry.event_genres = form.cleaned_data['event_genres']
            calendar_entry.save()
            messages.success(self.request, '集会情報とVRCイベントカレンダー用情報が更新されました。')
            return response
        except DataError as e:
            messages.error(self.request, f'データの保存中にエラーが発生しました: {str(e)}')
            return self.form_invalid(form)


class WaitingCommunityListView(LoginRequiredMixin, ListView):
    model = Community
    template_name = 'community/waiting_list.html'
    context_object_name = 'communities'

    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = queryset.filter(is_accepted=False)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = CommunitySearchForm(self.request.GET)
        context['form'] = form
        context['search_count'] = self.get_queryset().count()
        for community in context['communities']:
            if community.twitter_hashtag:
                community.twitter_hashtags = [f'#{tag.strip()}' for tag in community.twitter_hashtag.split('#') if
                                              tag.strip()]
            community.join_type = get_join_type(community.organizer_url)

        # 曜日の選択肢をコンテキストに追加
        context['weekday_choices'] = dict(WEEKDAY_CHOICES)

        return context


class AcceptView(View):
    def post(self, request):
        # 自分の集会が承認されていない場合は権限がない
        if Community.objects.filter(custom_user=request.user, is_accepted=False).exists():
            messages.error(request, '権限がありません。')
            return redirect('community:waiting_list')
        # 承認する集会を取得、承認する
        community_id = request.POST.get('community_id')
        community = get_object_or_404(Community, pk=community_id)
        community.is_accepted = True
        community.save()
        messages.success(request, f'{community.name}を承認しました。')
        return redirect('community:waiting_list')
