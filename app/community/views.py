from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, DetailView
from event.models import Event
from .libs import get_join_type
from .forms import CommunitySearchForm
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.urls import reverse_lazy
from django.views.generic import UpdateView
from .models import Community
from .forms import CommunityUpdateForm


class CommunityListView(ListView):
    model = Community
    template_name = 'community/list.html'
    context_object_name = 'communities'
    paginate_by = 20

    def get_queryset(self):
        queryset = super().get_queryset()
        now = timezone.now()
        queryset = queryset.filter(is_accepted=True, end_at__isnull=True)
        form = CommunitySearchForm(self.request.GET)
        if form.is_valid():
            if query := form.cleaned_data['query']:
                queryset = queryset.filter(Q(name__icontains=query) | Q(description__icontains=query))
            if weekdays := form.cleaned_data['weekdays']:
                for weekday in weekdays:
                    queryset = queryset.filter(weekdays__contains=[weekday])
            if tags := form.cleaned_data['tags']:
                for tag in tags:
                    queryset = queryset.filter(tags__contains=[tag])
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

        # 曜日の選択肢をコンテキストに追加
        context['weekday_choices'] = dict(WEEKDAY_CHOICES)

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
            community=community, date__gte=now).select_related('details').order_by('date', 'start_time')[:4]

        # 過去のイベントを取得。ただし、event_detailが存在するもののみ
        context['past_events'] = Event.objects.filter(
            community=community, date__lt=now
        ).filter(
            Q(details__theme__isnull=False) | Q(details__theme__gt='')
        ).select_related('details').order_by('-date', '-start_time')[:4]

        # 曜日の選択肢をコンテキストに追加
        context['weekday_choices'] = dict(WEEKDAY_CHOICES)

        # タグの選択肢をコンテキストに追加
        context['tag_choices'] = dict(TAGS)

        # 承認ボタンの表示
        if self.request.user != community.custom_user and not community.is_accepted:
            context['show_accept_button'] = True
        return context


class CommunityUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Community
    form_class = CommunityUpdateForm
    template_name = 'community/update.html'
    success_url = reverse_lazy('account:settings')

    def test_func(self):
        community = self.get_object()
        return self.request.user == community.custom_user

    def form_valid(self, form):
        messages.success(self.request, 'コミュニティ情報が更新されました。')
        return super().form_valid(form)


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
