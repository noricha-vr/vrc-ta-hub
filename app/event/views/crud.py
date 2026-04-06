from datetime import date, datetime

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.cache import cache
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, FormView, UpdateView
from django.views.generic.edit import DeleteView

from event.forms import EventDetailForm, GoogleCalendarEventForm
from event.models import Event, EventDetail
from website.settings import (
    GEMINI_MODEL,
    GOOGLE_CALENDAR_CREDENTIALS,
    GOOGLE_CALENDAR_ID,
)

from ..google_calendar import GoogleCalendarService
from .compat import get_generate_blog, get_logger
from .helpers import can_manage_event_detail


class EventDeleteView(LoginRequiredMixin, DeleteView):
    model = Event
    success_url = reverse_lazy("event:my_list")

    def post(self, request, *args, **kwargs):
        event = self.get_object()

        if not (request.user.is_superuser or request.user.is_staff):
            from vket.services import get_vket_lock_info

            locked, message = get_vket_lock_info(event)
            if locked:
                messages.error(request, message)
                return redirect("event:my_list")

        if not event.community.can_delete(request.user):
            messages.error(request, "このイベントを削除する権限がありません。")
            return redirect("event:my_list")

        user_community = event.community
        logger = get_logger()

        logger.info(
            "イベント削除開始: "
            f"ID={event.id}, コミュニティ={event.community.name}, "
            f"日付={event.date}, 開始時間={event.start_time}"
        )
        logger.info(f"Google Calendar Event ID: {event.google_calendar_event_id}")

        delete_subsequent = request.POST.get("delete_subsequent") == "on"
        events_to_delete = [event]

        if delete_subsequent:
            subsequent_events = Event.objects.filter(
                community=user_community,
                date__gt=event.date,
            ).order_by("date", "start_time")
            events_to_delete.extend(subsequent_events)
            logger.info(f"以降のイベントも削除します: {len(subsequent_events)}件")

        if not (request.user.is_superuser or request.user.is_staff):
            from vket.services import get_vket_lock_info

            locked_events = []
            lock_message = ""
            for event_to_delete in events_to_delete:
                locked, message = get_vket_lock_info(event_to_delete)
                if locked:
                    locked_events.append(event_to_delete)
                    lock_message = message
            if locked_events:
                events_to_delete = [
                    event_to_delete
                    for event_to_delete in events_to_delete
                    if event_to_delete not in locked_events
                ]
                messages.warning(
                    request,
                    f"{lock_message} ロック中のイベント{len(locked_events)}件をスキップしました。",
                )
                if not events_to_delete:
                    return redirect("event:my_list")

        success_count = 0
        error_count = 0

        for event_to_delete in events_to_delete:
            try:
                if event_to_delete.google_calendar_event_id:
                    try:
                        calendar_service = GoogleCalendarService(
                            calendar_id=GOOGLE_CALENDAR_ID,
                            credentials_path=GOOGLE_CALENDAR_CREDENTIALS,
                        )
                        logger.info(
                            "Googleカレンダーからの削除を試行: "
                            f"Event ID={event_to_delete.google_calendar_event_id}"
                        )
                        calendar_service.delete_event(
                            event_to_delete.google_calendar_event_id
                        )
                        logger.info(
                            "Googleカレンダーからの削除成功: "
                            f"Event ID={event_to_delete.google_calendar_event_id}"
                        )
                    except Exception as exc:
                        logger.error(
                            "Googleカレンダーからの削除失敗: "
                            f"Event ID={event_to_delete.google_calendar_event_id}, "
                            f"エラー={str(exc)}"
                        )
                        error_count += 1
                        continue

                event_to_delete.delete()
                success_count += 1
                logger.info(f"データベースからの削除成功: ID={event_to_delete.id}")
            except Exception as exc:
                logger.error(
                    f"イベントの削除に失敗: ID={event_to_delete.id}, エラー={str(exc)}"
                )
                error_count += 1

        if success_count > 0:
            if delete_subsequent:
                messages.success(request, f"{success_count}件のイベントを削除しました。")
            else:
                messages.success(request, "イベントを削除しました。")

        if error_count > 0:
            messages.error(
                request,
                f"{error_count}件のイベントの削除中にエラーが発生しました。",
            )

        return redirect("event:my_list")


class EventDetailCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = EventDetail
    form_class = EventDetailForm
    template_name = "event/detail_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.event = get_object_or_404(Event, pk=kwargs["event_pk"])
        return super().dispatch(request, *args, **kwargs)

    def test_func(self):
        return self.request.user.is_superuser or self.event.community.can_edit(
            self.request.user
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["event"] = self.event
        context["is_before_event"] = self.event.date > date.today()
        return context

    def form_valid(self, form):
        form.instance.event = self.event
        response = super().form_valid(form)

        generate_blog_flag = form.cleaned_data.get("generate_blog_article", False)
        if (
            generate_blog_flag
            and form.instance.detail_type == "LT"
            and (form.instance.slide_file or form.instance.youtube_url)
        ):
            try:
                blog_output = get_generate_blog()(form.instance, model=GEMINI_MODEL)
                if blog_output.title:
                    form.instance.h1 = blog_output.title
                    form.instance.contents = blog_output.text
                    form.instance.meta_description = blog_output.meta_description
                    form.instance.save()
                    messages.success(self.request, "記事を自動生成しました。")
                    get_logger().info(f"記事を自動生成しました: {form.instance.id}")
                else:
                    get_logger().warning(
                        f"記事の自動生成に失敗しました（空の結果）: {form.instance.id}"
                    )
                    messages.warning(self.request, "記事の自動生成に失敗しました。")
            except Exception:
                get_logger().exception("記事の自動生成中にエラーが発生しました")
                messages.error(self.request, "記事の自動生成中にエラーが発生しました")

        return response

    def get_success_url(self):
        return reverse_lazy("event:detail", kwargs={"pk": self.object.pk})


class EventDetailUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = EventDetail
    form_class = EventDetailForm
    template_name = "event/detail_form.html"

    def test_func(self):
        event_detail = self.get_object()
        # 発表者本人は自分の承認済みLTのみ更新可。参照: PR #116（発表者フローを保ちつつ権限範囲を限定するため）
        return can_manage_event_detail(self.request.user, event_detail)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            return redirect("event:detail", pk=self.get_object().pk)
        return super().handle_no_permission()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["event"] = self.object.event
        context["is_before_event"] = self.object.event.date > date.today()
        return context

    def form_valid(self, form):
        response = super().form_valid(form)

        generate_blog_flag = form.cleaned_data.get("generate_blog_article", False)
        if (
            generate_blog_flag
            and form.instance.detail_type == "LT"
            and (form.instance.slide_file or form.instance.youtube_url)
        ):
            try:
                blog_output = get_generate_blog()(form.instance, model=GEMINI_MODEL)
                if blog_output.title:
                    form.instance.h1 = blog_output.title
                    form.instance.contents = blog_output.text
                    form.instance.meta_description = blog_output.meta_description
                    form.instance.save()
                    messages.success(self.request, "記事を自動生成しました。")
                    get_logger().info(f"記事を自動生成しました: {form.instance.id}")
                else:
                    get_logger().warning(
                        f"記事の自動生成に失敗しました（空の結果）: {form.instance.id}"
                    )
                    messages.warning(self.request, "記事の自動生成に失敗しました。")
            except Exception:
                get_logger().exception("記事の自動生成中にエラーが発生しました")
                messages.error(self.request, "記事の自動生成中にエラーが発生しました")

        return response

    def get_success_url(self):
        return reverse_lazy("event:detail", kwargs={"pk": self.object.pk})


class EventDetailDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = EventDetail
    template_name = "event/detail_confirm_delete.html"

    def test_func(self):
        event_detail = self.get_object()
        return self.request.user.is_superuser or event_detail.event.community.can_edit(
            self.request.user
        )

    def post(self, request, *args, **kwargs):
        event_detail = self.get_object()
        if not (request.user.is_superuser or request.user.is_staff):
            from vket.services import get_vket_lock_info

            locked, message = get_vket_lock_info(event_detail.event)
            if locked:
                messages.error(request, message)
                return redirect("event:detail", pk=event_detail.pk)
        return super().post(request, *args, **kwargs)

    def get_success_url(self):
        return reverse_lazy("event:my_list")


class GoogleCalendarEventCreateView(LoginRequiredMixin, FormView):
    template_name = "event/calendar_form.html"
    form_class = GoogleCalendarEventForm
    success_url = reverse_lazy("event:my_list")

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

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)

        community = self._get_active_community()
        if not community or community.status != "approved":
            messages.error(
                request,
                "集会が承認されていないため、カレンダーにイベントを登録できません。",
            )
            return redirect("event:my_list")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if self.request.user.is_authenticated:
            community = self._get_active_community()
            if community:
                kwargs["initial"] = {
                    "start_time": community.start_time,
                    "duration": community.duration,
                }
        return kwargs

    def form_valid(self, form):
        logger = get_logger()
        try:
            community = self._get_active_community()
            if not community:
                messages.error(self.request, "コミュニティが見つかりません")
                return self.form_invalid(form)

            start_date = form.cleaned_data["start_date"]
            start_time = form.cleaned_data["start_time"]

            logger.info(
                f"イベント登録開始: コミュニティ={community.name}, "
                f"日付={start_date}, 開始時間={start_time}"
            )

            existing_event = Event.objects.filter(
                date=start_date,
                start_time=start_time,
                community=community,
            ).first()

            if existing_event:
                logger.warning(
                    f"重複イベント検出: ID={existing_event.id}, "
                    f"コミュニティ={community.name}, 日付={start_date}, "
                    f"開始時間={start_time}"
                )
                messages.error(
                    self.request,
                    f"同じ日時（{start_date} {start_time}）にすでにイベントが登録されています。",
                )
                return self.form_invalid(form)

            start_datetime = datetime.combine(start_date, start_time)
            duration = form.cleaned_data["duration"]

            try:
                new_event = Event.objects.create(
                    community=community,
                    date=start_date,
                    start_time=start_time,
                    duration=duration,
                    weekday=start_datetime.strftime("%a"),
                )
                logger.info(
                    f"イベントをDBに登録: ID={new_event.id}, 日付={start_date}, 開始時間={start_time}"
                )

                cache_key = f"calendar_entry_url_{new_event.id}"
                cache.delete(cache_key)

                messages.success(self.request, "イベントが正常に登録されました")
            except Exception as exc:
                logger.error(f"イベントのDB登録でエラー: {str(exc)}", exc_info=True)
                messages.error(self.request, "イベントの登録に失敗しました")
                return self.form_invalid(form)

            return super().form_valid(form)
        except Exception:
            logger.exception("イベントの登録に失敗しました")
            messages.error(self.request, "イベントの登録に失敗しました")
            return self.form_invalid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["community"] = self._get_active_community()
        return context
