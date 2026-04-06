from datetime import timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.db.models import Prefetch, QuerySet
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import ListView

from event.forms import EventSearchForm
from event.models import Event, EventDetail
from event_calendar.calendar_utils import (
    create_calendar_entry_url,
    generate_google_calendar_url,
)
from ta_hub.utils import get_client_ip
from url_filters import get_filtered_url
from utils.vrchat_time import get_vrchat_today
from website.settings import GOOGLE_CALENDAR_ID


class EventListView(ListView):
    model = Event
    template_name = "event/list.html"
    context_object_name = "events"
    paginate_by = 30

    def get(self, request, *args, **kwargs):
        page_str = request.GET.get("page", "1")

        try:
            page = int("".join(filter(str.isdigit, page_str)) or "1")
        except (ValueError, TypeError):
            params = request.GET.copy()
            params["page"] = "1"
            return redirect(f"{request.path}?{params.urlencode()}")

        self.object_list = self.get_queryset()
        paginator = self.get_paginator(self.object_list, self.paginate_by)

        if page > paginator.num_pages and paginator.num_pages > 0:
            params = request.GET.copy()
            params["page"] = "1"
            return redirect(f"{request.path}?{params.urlencode()}")

        request.GET = request.GET.copy()
        request.GET["page"] = str(page)
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset()
        today = get_vrchat_today()
        queryset = (
            queryset.filter(
                date__gte=today,
                community__status="approved",
                community__end_at__isnull=True,
            )
            .select_related("community")
            .prefetch_related(
                Prefetch(
                    "details",
                    queryset=EventDetail.objects.filter(status="approved"),
                )
            )
            .order_by("date", "start_time")
        )

        form = EventSearchForm(self.request.GET)
        if form.is_valid():
            if name := form.cleaned_data.get("name"):
                queryset = queryset.filter(community__name__icontains=name)

            if weekdays := form.cleaned_data.get("weekday"):
                queryset = queryset.filter(weekday__in=weekdays)

            if tags := form.cleaned_data["tags"]:
                for tag in tags:
                    queryset = queryset.filter(community__tags__contains=[tag])

        for event in queryset:
            event.google_calendar_url = generate_google_calendar_url(self.request, event)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = EventSearchForm(self.request.GET or None)
        context["selected_weekdays"] = self.request.GET.getlist("weekday")
        context["selected_tags"] = self.request.GET.getlist("tags")

        base_url = reverse("event:list")
        current_params = self.request.GET.copy()

        query_params_for_pagination = current_params.copy()
        if "page" in query_params_for_pagination:
            del query_params_for_pagination["page"]
        context["current_query_params"] = query_params_for_pagination.urlencode()

        context["weekday_urls"] = {
            choice[0]: get_filtered_url(base_url, current_params, "weekday", choice[0])
            for choice in context["form"].fields["weekday"].choices
        }
        context["tag_urls"] = {
            choice[0]: get_filtered_url(base_url, current_params, "tags", choice[0])
            for choice in context["form"].fields["tags"].choices
        }

        context["google_calendar_id"] = GOOGLE_CALENDAR_ID
        return context


class EventMyList(LoginRequiredMixin, ListView):
    model = Event
    template_name = "event/my_list.html"
    context_object_name = "events"
    paginate_by = 20

    def _get_user_communities(self):
        return list(
            self.request.user.community_memberships.values_list(
                "community_id",
                flat=True,
            )
        )

    def _get_active_community(self):
        active_community_id = self.request.session.get("active_community_id")
        if active_community_id:
            membership = (
                self.request.user.community_memberships.filter(
                    community_id=active_community_id
                )
                .select_related("community")
                .first()
            )
            if membership:
                return membership.community

        membership = (
            self.request.user.community_memberships.select_related("community").first()
        )
        if membership:
            return membership.community

        return None

    def _get_user_communities_list(self):
        communities = []
        for membership in self.request.user.community_memberships.select_related(
            "community"
        ):
            communities.append(membership.community)
        return communities

    def _get_warnings(self, community):
        warnings = []
        if not community:
            return warnings

        if not community.poster_image:
            warnings.append(
                {
                    "type": "warning",
                    "message": (
                        "ポスター画像が設定されていません。"
                        "ポスター画像を設定しないと、集会一覧やトップページにイベントが表示されません。"
                    ),
                    "link": reverse("community:update"),
                    "link_text": "設定する",
                }
            )

        future_events = Event.objects.filter(
            community=community,
            date__gte=timezone.now().date(),
        ).exists()
        if not future_events:
            warnings.append(
                {
                    "type": "info",
                    "message": "今後のイベントが登録されていません。",
                    "link": reverse("event:calendar_create"),
                    "link_text": "イベントを登録",
                }
            )

        return warnings

    def get_queryset(self):
        today = get_vrchat_today()
        user_community_ids = self._get_user_communities()

        active_community_id = self.request.session.get("active_community_id")
        if active_community_id and active_community_id in user_community_ids:
            community_ids = [active_community_id]
        else:
            community_ids = user_community_ids

        future_events = (
            Event.objects.filter(
                community_id__in=community_ids,
                date__gte=today,
            )
            .select_related("community")
            .order_by("date", "start_time")[:2]
        )

        past_events = (
            Event.objects.filter(
                community_id__in=community_ids,
                date__lt=today,
            )
            .select_related("community")
            .order_by("-date", "-start_time")
        )

        return list(future_events) + list(past_events)

    def set_vrc_event_calendar_post_url(self, queryset: QuerySet) -> QuerySet:
        for event in queryset:
            if get_vrchat_today() > event.date:
                continue
            event.calendar_url = create_calendar_entry_url(event)
        return queryset

    def _set_twitter_button_flags(self, events):
        today = get_vrchat_today()
        for event in events:
            twitter_display_until = event.date + timedelta(days=7)
            event.twitter_button_active = today <= twitter_display_until
        return events

    def _attach_event_details(self, events):
        event_ids = [event.id for event in events]

        if event_ids:
            event_details = (
                EventDetail.objects.filter(event_id__in=event_ids)
                .select_related("event")
                .order_by("created_at")
            )

            event_detail_dict = {}
            for detail in event_details:
                if detail.event_id not in event_detail_dict:
                    event_detail_dict[detail.event_id] = []
                event_detail_dict[detail.event_id].append(detail)

            for event in events:
                event.detail_list = event_detail_dict.get(event.id, [])
        else:
            for event in events:
                event.detail_list = []

        return events

    def _prepare_pagination_params(self):
        query_params = self.request.GET.copy()
        if "page" in query_params:
            del query_params["page"]
        return query_params.urlencode()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        active_community = self._get_active_community()
        context["community"] = active_community
        context["active_community"] = active_community
        context["communities"] = self._get_user_communities_list()
        context["warnings"] = self._get_warnings(active_community)

        events = context["events"]
        events = self.set_vrc_event_calendar_post_url(events)
        events = self._set_twitter_button_flags(events)
        events = self._attach_event_details(events)
        context["events"] = events

        context["current_query_params"] = self._prepare_pagination_params()
        context["vket_banner"] = self._get_vket_banner(active_community)

        today = get_vrchat_today()
        context["has_future_events"] = any(event.date >= today for event in events)
        return context

    def _get_vket_banner(self, community):
        from vket.models import VketCollaboration, VketParticipation

        collaboration = (
            VketCollaboration.objects.exclude(
                phase__in=[
                    VketCollaboration.Phase.DRAFT,
                    VketCollaboration.Phase.ARCHIVED,
                ]
            )
            .order_by("-period_start", "-id")
            .first()
        )
        if not collaboration:
            return None

        today = timezone.localdate()

        has_participation = False
        if community:
            has_participation = VketParticipation.objects.filter(
                collaboration=collaboration,
                community=community,
            ).exists()

        is_during_event = collaboration.period_start <= today <= collaboration.period_end

        phase = collaboration.phase
        period = (
            f"{collaboration.period_start.month}/{collaboration.period_start.day}"
            f"〜{collaboration.period_end.month}/{collaboration.period_end.day}"
        )
        if is_during_event:
            message = (
                f"{collaboration.name} 開催中！"
                f"（{collaboration.period_end.month}/{collaboration.period_end.day}まで）"
            )
        elif phase == VketCollaboration.Phase.ENTRY_OPEN:
            message = f"{collaboration.name}（{period}）参加申し込み受付中"
        elif phase in (
            VketCollaboration.Phase.SCHEDULING,
            VketCollaboration.Phase.LT_COLLECTION,
        ):
            message = f"{collaboration.name}（{period}）"
        elif phase in (
            VketCollaboration.Phase.ANNOUNCEMENT,
            VketCollaboration.Phase.LOCKED,
        ):
            message = f"{collaboration.name}（{period}）"
        else:
            return None

        if not has_participation and phase == VketCollaboration.Phase.ENTRY_OPEN:
            url_name = "vket:apply"
            button_text = "参加申し込み"
        else:
            url_name = "vket:status"
            button_text = "参加状況を確認"

        return {
            "collaboration": collaboration,
            "message": message,
            "url_name": url_name,
            "url_pk": collaboration.pk,
            "button_text": button_text,
            "has_participation": has_participation,
        }


class EventDetailPastList(ListView):
    template_name = "event/detail_history.html"
    model = EventDetail
    context_object_name = "event_details"
    paginate_by = 20
    RATE_LIMIT_WINDOW_SECONDS = 10 * 60
    RATE_LIMIT_MAX_REQUESTS = 20
    ALLOWED_FILTER_KEYS = ("community_name", "speaker", "theme")

    def _get_rate_limit_cache_key(self, client_ip):
        bucket = int(timezone.now().timestamp()) // self.RATE_LIMIT_WINDOW_SECONDS
        return f"event_detail_history:ip:{client_ip}:bucket:{bucket}"

    def _is_rate_limited(self):
        client_ip = get_client_ip(self.request)
        cache_key = self._get_rate_limit_cache_key(client_ip)
        request_count = cache.get(cache_key, 0)

        if request_count >= self.RATE_LIMIT_MAX_REQUESTS:
            return True

        if request_count == 0:
            cache.set(cache_key, 1, timeout=self.RATE_LIMIT_WINDOW_SECONDS)
        else:
            try:
                cache.incr(cache_key)
            except ValueError:
                cache.set(cache_key, 1, timeout=self.RATE_LIMIT_WINDOW_SECONDS)

        return False

    def _get_sanitized_filter_params(self):
        params = self.request.GET.copy()

        for key in list(params.keys()):
            if key not in self.ALLOWED_FILTER_KEYS:
                del params[key]

        for key in self.ALLOWED_FILTER_KEYS:
            value = params.get(key, "").strip()
            if value:
                params.setlist(key, [value])
            elif key in params:
                del params[key]

        return params

    def dispatch(self, request, *args, **kwargs):
        if self._is_rate_limited():
            return HttpResponse(
                "アクセスが集中しています。しばらくしてから再度お試しください。",
                status=429,
                content_type="text/plain; charset=utf-8",
            )
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        page_str = request.GET.get("page", "1")

        try:
            page = int("".join(filter(str.isdigit, page_str)) or "1")
        except (ValueError, TypeError):
            params = request.GET.copy()
            params["page"] = "1"
            return redirect(f"{request.path}?{params.urlencode()}")

        self.object_list = self.get_queryset()
        paginator = self.get_paginator(self.object_list, self.paginate_by)

        if page > paginator.num_pages and paginator.num_pages > 0:
            params = request.GET.copy()
            params["page"] = "1"
            return redirect(f"{request.path}?{params.urlencode()}")

        request.GET = request.GET.copy()
        request.GET["page"] = str(page)
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .filter(
                detail_type="LT",
                status="approved",
            )
            .select_related("event", "event__community")
            .order_by("-event__date", "-start_time")
        )

        community_name = self.request.GET.get("community_name", "").strip()
        if community_name:
            queryset = queryset.filter(event__community__name__icontains=community_name)

        speaker = self.request.GET.get("speaker", "").strip()
        if speaker:
            queryset = queryset.filter(speaker__icontains=speaker)

        theme = self.request.GET.get("theme", "").strip()
        if theme:
            queryset = queryset.filter(theme__icontains=theme)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        query_params = self._get_sanitized_filter_params()
        context["current_query_params"] = query_params.urlencode()

        speaker_link_base_params = query_params.copy()
        if "speaker" in speaker_link_base_params:
            del speaker_link_base_params["speaker"]
        context["speaker_link_base_query"] = speaker_link_base_params.urlencode()

        community_link_base_params = query_params.copy()
        if "community_name" in community_link_base_params:
            del community_link_base_params["community_name"]
        context["community_link_base_query"] = community_link_base_params.urlencode()

        return context


class EventLogListView(ListView):
    """特別企画とブログの一覧表示"""

    template_name = "event/event_log_list.html"
    model = EventDetail
    context_object_name = "event_logs"
    paginate_by = 20

    def get(self, request, *args, **kwargs):
        page_str = request.GET.get("page", "1")

        try:
            page = int("".join(filter(str.isdigit, page_str)) or "1")
        except (ValueError, TypeError):
            params = request.GET.copy()
            params["page"] = "1"
            return redirect(f"{request.path}?{params.urlencode()}")

        self.object_list = self.get_queryset()
        paginator = self.get_paginator(self.object_list, self.paginate_by)

        if page > paginator.num_pages and paginator.num_pages > 0:
            params = request.GET.copy()
            params["page"] = "1"
            return redirect(f"{request.path}?{params.urlencode()}")

        request.GET = request.GET.copy()
        request.GET["page"] = str(page)
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .filter(
                detail_type__in=["SPECIAL", "BLOG"],
                status="approved",
            )
            .select_related("event", "event__community")
            .order_by("-event__date", "-start_time")
        )

        community_name = self.request.GET.get("community_name", "").strip()
        if community_name:
            queryset = queryset.filter(event__community__name__icontains=community_name)

        theme = self.request.GET.get("theme", "").strip()
        if theme:
            queryset = queryset.filter(theme__icontains=theme)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query_params = self.request.GET.copy()
        if "page" in query_params:
            del query_params["page"]
        context["current_query_params"] = query_params.urlencode()
        return context
