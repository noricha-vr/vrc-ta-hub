from django.core.paginator import InvalidPage
from django.db.models import F, Min, Q
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import DetailView, ListView

from event.models import Event, EventDetail
from url_filters import get_filtered_url

from community.libs import get_join_type
from community.models import Community, TAGS, WEEKDAY_CHOICES

from ..forms import CommunitySearchForm

import community.views as community_views


class CommunityListView(ListView):
    model = Community
    template_name = 'community/list.html'
    context_object_name = 'communities'
    paginate_by = 18

    def get(self, request, *args, **kwargs):
        page = request.GET.get(self.page_kwarg) or 1
        self.object_list = self.get_queryset()

        paginator = self.get_paginator(self.object_list, self.paginate_by)
        if page == 'last':
            return super().get(request, *args, **kwargs)

        try:
            paginator.validate_number(page)
        except InvalidPage:
            params = request.GET.copy()
            params[self.page_kwarg] = 1
            return redirect(f"{request.path}?{params.urlencode()}")

        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset()
        now = timezone.now()
        queryset = queryset.filter(
            status='approved',
            end_at__isnull=True,
            poster_image__isnull=False,
        ).exclude(poster_image='')

        queryset = queryset.annotate(
            latest_event_date=Min(
                'events__date',
                filter=Q(events__date__gte=now.date()),
            )
        )

        form = CommunitySearchForm(self.request.GET)
        if form.is_valid():
            if query := form.cleaned_data['query']:
                queryset = queryset.filter(
                    Q(name__icontains=query) | Q(description__icontains=query)
                )
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

        queryset = queryset.order_by(
            F('latest_event_date').asc(nulls_last=True),
            '-updated_at',
        )
        community_views.logger.info(f'検索結果: {queryset.count()}件')
        if queryset.count() == 0:
            community_views.logger.info('現在開催中の集会はありません。')
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = CommunitySearchForm(self.request.GET or None)
        context['selected_weekdays'] = self.request.GET.getlist('weekdays')
        context['selected_tags'] = self.request.GET.getlist('tags')

        base_url = reverse('community:list')
        current_params = self.request.GET.copy()

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


class CommunityDetailView(DetailView):
    model = Community
    template_name = 'community/detail.html'
    context_object_name = 'community'

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.user.is_superuser:
            return queryset
        return queryset.filter(status='approved')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        community = context['community']
        community.join_type = get_join_type(community.organizer_url)

        now = timezone.now()

        scheduled_events = Event.objects.filter(
            community=community,
            date__gte=now,
        ).prefetch_related('details').order_by('date', 'start_time')[:4]
        context['scheduled_events'] = self.get_event_details(scheduled_events)

        past_events = Event.objects.filter(
            community=community,
            date__lt=now,
        ).filter(
            Q(details__theme__isnull=False) | Q(details__theme__gt='')
        ).prefetch_related('details').order_by('-date', '-start_time')[:6]
        context['past_events'] = self.get_event_details(past_events)

        context['blog_special_details'] = EventDetail.objects.filter(
            event__community=community,
            detail_type__in=['BLOG', 'SPECIAL'],
            status='approved',
        ).select_related('event').order_by('-event__date')[:6]

        context['weekday_choices'] = dict(WEEKDAY_CHOICES)
        context['tag_choices'] = dict(TAGS)

        if self.request.user.is_superuser and community.status == 'pending':
            context['show_accept_button'] = True
            context['show_reject_button'] = True

        if self.request.user.is_authenticated:
            context['is_owner'] = community.is_owner(self.request.user)
        else:
            context['is_owner'] = False

        if self.request.user.is_superuser:
            from allauth.socialaccount.models import SocialAccount

            owner = community.get_owner()
            discord_account = SocialAccount.objects.filter(
                user=owner,
                provider='discord',
            ).first() if owner else None
            context['owner_discord_id'] = discord_account.uid if discord_account else None
            context['owner_email'] = community.get_owner_email()

        return context

    def get_event_details(self, events):
        event_details_list = []
        last_event = None
        for event in events:
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
        queryset = queryset.filter(
            status='approved',
            end_at__isnull=False,
        ).order_by('-end_at')

        form = CommunitySearchForm(self.request.GET)
        if form.is_valid():
            if query := form.cleaned_data['query']:
                queryset = queryset.filter(
                    Q(name__icontains=query) | Q(description__icontains=query)
                )
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
