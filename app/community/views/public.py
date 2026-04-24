"""公開ページ: 集会一覧、集会詳細、アーカイブ一覧."""
import logging

from django.core.paginator import InvalidPage
from django.db.models import Q, F, OuterRef, Subquery
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import ListView, DetailView

from event.models import Event, EventDetail
from url_filters import get_filtered_url

from ..forms import CommunitySearchForm
from ..libs import get_join_type
from ..models import Community, WEEKDAY_CHOICES, TAGS

logger = logging.getLogger(__name__)


class CommunityListView(ListView):
    model = Community
    template_name = 'community/list.html'
    context_object_name = 'communities'
    paginate_by = 18

    def get(self, request, *args, **kwargs):
        # 通常のget処理の前にページ番号をチェック
        page = request.GET.get(self.page_kwarg) or 1
        self.object_list = self.get_queryset()

        paginator = self.get_paginator(self.object_list, self.paginate_by)
        if page == 'last':
            return super().get(request, *args, **kwargs)

        try:
            paginator.validate_number(page)
        except InvalidPage:
            # クエリパラメータを維持したまま1ページ目にリダイレクト
            params = request.GET.copy()
            params[self.page_kwarg] = 1
            return redirect(f"{request.path}?{params.urlencode()}")

        return super().get(request, *args, **kwargs)

    def get_filtered_queryset(self):
        if hasattr(self, '_filtered_queryset'):
            return self._filtered_queryset

        queryset = super().get_queryset()
        # 承認済みでアクティブな集会（終了日がない）、かつポスター画像がある
        queryset = queryset.filter(
            status='approved',
            end_at__isnull=True,
            poster_image__isnull=False
        ).exclude(poster_image='')

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

        self._filtered_queryset = queryset
        return queryset

    def get_search_count(self):
        if not hasattr(self, '_search_count'):
            self._search_count = self.get_filtered_queryset().count()
        return self._search_count

    def get_queryset(self):
        if hasattr(self, '_ordered_queryset'):
            return self._ordered_queryset

        queryset = self.get_filtered_queryset()
        now = timezone.now()

        latest_event_date = Event.objects.filter(
            community=OuterRef('pk'),
            date__gte=now.date(),
        ).order_by('date').values('date')[:1]

        queryset = queryset.annotate(
            latest_event_date=Subquery(latest_event_date)
        )

        # 最新のイベント日でソート（NULL値は最後に）
        queryset = queryset.order_by(
            F('latest_event_date').asc(nulls_last=True),
            '-updated_at'
        )
        search_count = self.get_search_count()
        logger.info(f'検索結果: {search_count}件')
        if search_count == 0:
            logger.info('現在開催中の集会はありません。')
        self._ordered_queryset = queryset
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = CommunitySearchForm(self.request.GET or None)
        context['selected_weekdays'] = self.request.GET.getlist('weekdays')
        context['selected_tags'] = self.request.GET.getlist('tags')

        base_url = reverse('community:list')
        current_params = self.request.GET.copy()

        # ページネーションリンク用に既存の 'page' パラメータを削除
        query_params_for_pagination = current_params.copy()
        if 'page' in query_params_for_pagination:
            del query_params_for_pagination['page']
        context['current_query_params'] = query_params_for_pagination.urlencode()

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
        context['search_count'] = self.get_search_count()
        return context


class CommunityDetailView(DetailView):
    model = Community
    template_name = 'community/detail.html'
    context_object_name = 'community'

    def get_queryset(self):
        queryset = super().get_queryset()
        # 承認済み以外はsuperuserのみ閲覧可
        if self.request.user.is_superuser:
            return queryset
        return queryset.filter(status='approved')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        community = context['community']
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

        # BLOG・特別企画の記事を取得
        blog_special_details = EventDetail.objects.filter(
            event__community=community,
            detail_type__in=['BLOG', 'SPECIAL'],
            status='approved',
        ).select_related('event').order_by('-event__date')[:6]
        context['blog_special_details'] = blog_special_details

        # 曜日の選択肢をコンテキストに追加
        context['weekday_choices'] = dict(WEEKDAY_CHOICES)

        # タグの選択肢をコンテキストに追加
        context['tag_choices'] = dict(TAGS)

        # 承認ボタンの表示
        if self.request.user.is_superuser and community.status == 'pending':
            context['show_accept_button'] = True
            context['show_reject_button'] = True

        # ユーザーが主催者かどうかを判定
        if self.request.user.is_authenticated:
            context['is_owner'] = community.is_owner(self.request.user)
        else:
            context['is_owner'] = False

        # superuserの場合、集会オーナーのDiscord IDとメールアドレスをSocialAccountから取得
        if self.request.user.is_superuser:
            from allauth.socialaccount.models import SocialAccount
            owner = community.get_owner()
            discord_account = SocialAccount.objects.filter(
                user=owner,
                provider='discord'
            ).first() if owner else None
            context['owner_discord_id'] = discord_account.uid if discord_account else None
            context['owner_email'] = community.get_owner_email()

        return context

    def get_event_details(self, events):
        event_details_list = []
        last_event = None
        for event in events:
            # 承認済みのEventDetailのみ表示
            details = event.details.filter(status='approved', detail_type='LT')
            if event == last_event:
                continue
            event_details_list.append({
                'details': details,
                'event': event,
            })
            last_event = event
        return event_details_list


class ArchivedCommunityListView(ListView):
    model = Community
    template_name = 'community/archive_list.html'
    context_object_name = 'communities'
    paginate_by = 18

    def get_queryset(self):
        queryset = super().get_queryset()
        # 承認済みで閉鎖された集会のみ表示
        queryset = queryset.filter(
            status='approved',
            end_at__isnull=False
        ).order_by('-end_at')

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
                tag_filters = Q()
                for tag in tags:
                    tag_filters |= Q(tags__contains=[tag])
                queryset = queryset.filter(tag_filters)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = CommunitySearchForm(self.request.GET or None)
        context['selected_weekdays'] = self.request.GET.getlist('weekdays')
        context['selected_tags'] = self.request.GET.getlist('tags')

        base_url = reverse('community:archive_list')
        current_params = self.request.GET.copy()

        # ページネーションリンク用に既存の 'page' パラメータを削除
        query_params_for_pagination = current_params.copy()
        if 'page' in query_params_for_pagination:
            del query_params_for_pagination['page']
        context['current_query_params'] = query_params_for_pagination.urlencode()

        context['weekday_urls'] = {
            choice[0]: get_filtered_url(base_url, current_params, 'weekdays', choice[0])
            for choice in context['form'].fields['weekdays'].choices
        }
        context['tag_urls'] = {
            choice[0]: get_filtered_url(base_url, current_params, 'tags', choice[0])
            for choice in context['form'].fields['tags'].choices
        }

        context['weekday_choices'] = dict(WEEKDAY_CHOICES)
        context['tag_choices'] = dict(TAGS)
        context['search_count'] = self.get_queryset().count()
        return context
