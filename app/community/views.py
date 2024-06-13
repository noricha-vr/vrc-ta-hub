from django.db.models import Q
from django.utils import timezone
from django.views.generic import ListView, DetailView

from event.models import Event
from .libs import get_join_type
from .models import Community

from .forms import CommunitySearchForm


class CommunityListView(ListView):
    model = Community
    template_name = 'community/list.html'
    context_object_name = 'communities'
    paginate_by = 20

    def get_queryset(self):
        queryset = super().get_queryset()
        form = CommunitySearchForm(self.request.GET)
        if form.is_valid():
            query = form.cleaned_data['query']
            weekdays = form.cleaned_data['weekdays']
            if query:
                queryset = queryset.filter(Q(name__icontains=query) | Q(description__icontains=query))
            if weekdays:
                queryset = queryset.filter(weekday__in=weekdays)
        queryset = queryset.order_by('-updated_at')
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
        # 予定されているイベント scheduled events
        now = timezone.now()
        context['scheduled_events'] = Event.objects.filter(
            community=community, date__gte=now).prefetch_related('details').order_by('date', 'start_time')[:4]

        # 過去のイベントを取得。ただし、event_detailが存在するもののみ
        context['past_events'] = Event.objects.filter(
            community=community, date__lt=now
        ).filter(
            Q(details__theme__isnull=False) | Q(details__theme__gt='')
        ).prefetch_related('details').order_by('-date', '-start_time')[:4]

        # 曜日の選択肢をコンテキストに追加
        context['weekday_choices'] = dict(WEEKDAY_CHOICES)

        # タグの選択肢をコンテキストに追加
        context['tag_choices'] = dict(TAGS)

        return context
