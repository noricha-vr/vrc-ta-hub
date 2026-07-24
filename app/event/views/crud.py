import logging
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, UpdateView, DeleteView

from event.forms import EventDateUpdateForm, EventDetailForm
from event.services.content_generation_service import apply_blog_output_to_event_detail, generate_blog
from event.models import Event, EventDetail
from event.services.recurrence_override import (
    delete_event_with_tombstones,
    get_cascade_occurrences,
    move_event_occurrence,
)
from event.views.helpers import can_manage_event_detail
from ta_hub.access_mixins import AuthenticatedForbiddenMixin
from website.settings import GOOGLE_CALENDAR_CREDENTIALS, GOOGLE_CALENDAR_ID, GEMINI_MODEL
from event.google_calendar import GoogleCalendarService

logger = logging.getLogger(__name__)


class EventDateUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    """イベントの開始時刻を保ったまま開催日だけを変更する。"""

    model = Event
    form_class = EventDateUpdateForm
    template_name = 'event/date_form.html'
    success_url = reverse_lazy('event:my_list')

    def test_func(self):
        event = self.get_object()
        return (
            self.request.user.is_superuser
            or event.community.can_edit(self.request.user)
        )

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            messages.error(self.request, 'このイベントを編集する権限がありません。')
            return redirect('event:my_list')
        return super().handle_no_permission()

    def get_form_kwargs(self):
        """ModelFormがinstanceを書き換える前の開催日を保持する。"""
        self.original_date = self.object.date
        self.original_weekday = self.object.weekday
        return super().get_form_kwargs()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_recurring_child'] = (
            self.object.recurring_master_id is not None
        )
        context['is_recurring_master'] = self.object.is_recurring_master
        return context

    def form_valid(self, form):
        event = self.object
        new_date = form.cleaned_data['date']
        if new_date == self.original_date:
            messages.info(self.request, '開催日は変更されていません。')
            return redirect(self.success_url)

        self._restore_original_schedule(event)
        lock_message = self._get_vket_lock_message(event, new_date)
        if lock_message:
            form.add_error(None, lock_message)
            return self.form_invalid(form)

        move_event_occurrence(event, new_date)
        if event.google_calendar_event_id:
            try:
                self._update_google_calendar_event(event, new_date)
            except Exception:
                logger.exception(
                    'Google Calendarの日付更新に失敗: event_id=%s',
                    event.pk,
                )
                messages.warning(
                    self.request,
                    '開催日は変更しましたが、Googleカレンダーの更新に失敗しました。'
                    '後続の同期で再反映します。',
                )

        messages.success(self.request, 'イベントの開催日を変更しました。')
        return redirect(self.success_url)

    def _restore_original_schedule(self, event: Event) -> None:
        """ModelFormが未保存instanceへ反映した日付を変更前へ戻す。"""
        event.date = self.original_date
        event.weekday = self.original_weekday

    def _get_vket_lock_message(self, event: Event, new_date) -> str:
        """変更前後のどちらかがVketロック対象ならメッセージを返す。"""
        if self.request.user.is_superuser or self.request.user.is_staff:
            return ''

        from vket.services import get_vket_lock_info

        old_locked, old_message = get_vket_lock_info(event)
        new_locked, new_message = get_vket_lock_info(event, date=new_date)
        if old_locked or new_locked:
            return old_message or new_message
        return ''

    @staticmethod
    def _update_google_calendar_event(event: Event, new_date) -> None:
        start_at = datetime.combine(new_date, event.start_time)
        start_at = timezone.make_aware(
            start_at,
            timezone.get_current_timezone(),
        )
        calendar_service = GoogleCalendarService(
            calendar_id=GOOGLE_CALENDAR_ID,
            credentials_path=GOOGLE_CALENDAR_CREDENTIALS,
        )
        calendar_service.update_event(
            event_id=event.google_calendar_event_id,
            start_time=start_at,
            end_time=start_at + timedelta(minutes=event.duration),
        )


class EventDeleteView(LoginRequiredMixin, DeleteView):
    model = Event
    success_url = reverse_lazy('event:my_list')

    def post(self, request, *args, **kwargs):
        event = self.get_object()

        # Vketコラボ期間中は運営調整済みの開催日を守るため、主催者によるイベント削除をブロックする
        if not (request.user.is_superuser or request.user.is_staff):
            from vket.services import get_vket_lock_info
            locked, message = get_vket_lock_info(event)
            if locked:
                messages.error(request, message)
                return redirect('event:my_list')

        # イベントが属する集会に対する削除権限をチェック（主催者のみ）
        if not event.community.can_delete(request.user):
            messages.error(request, "このイベントを削除する権限がありません。")
            return redirect('event:my_list')

        # 削除対象のコミュニティを取得
        user_community = event.community

        logger.info(
            f"イベント削除開始: ID={event.id}, コミュニティ={event.community.name}, 日付={event.date}, 開始時間={event.start_time}")
        logger.info(f"Google Calendar Event ID: {event.google_calendar_event_id}")

        # 以降のイベントも削除するかどうかのチェック
        delete_subsequent = request.POST.get('delete_subsequent') == 'on'
        events_to_delete = [event]

        if delete_subsequent:
            # 同じコミュニティの、選択したイベント以降のイベントを取得
            # ユーザーのコミュニティのイベントのみに制限
            subsequent_events = Event.objects.filter(
                community=user_community,
                date__gt=event.date
            ).order_by('date', 'start_time')
            events_to_delete.extend(subsequent_events)
            logger.info(f"以降のイベントも削除します: {len(subsequent_events)}件")

        # 「以降のイベントも削除」選択時も、Vketコラボ期間中のイベントは運営調整済みのため対象から除外する
        if not (request.user.is_superuser or request.user.is_staff):
            from vket.services import get_vket_lock_info
            locked_events = []
            lock_message = ""
            for evt in events_to_delete:
                locked, message = get_vket_lock_info(evt)
                if locked:
                    locked_events.append(evt)
                    lock_message = message
            if locked_events:
                events_to_delete = [e for e in events_to_delete if e not in locked_events]
                messages.warning(
                    request,
                    f"{lock_message} ロック中のイベント{len(locked_events)}件をスキップしました。",
                )
                if not events_to_delete:
                    return redirect('event:my_list')

        success_count = 0
        error_count = 0
        google_error_count = 0
        processed_event_ids = set()

        for event_to_delete in events_to_delete:
            if event_to_delete.pk in processed_event_ids:
                continue
            occurrences = get_cascade_occurrences(event_to_delete)
            processed_event_ids.update(occurrence.pk for occurrence in occurrences)
            lock_message = self._get_cascade_lock_message(request, occurrences)
            if lock_message:
                messages.warning(
                    request,
                    f"{lock_message} 親イベントを含む削除を中止しました。",
                )
                continue

            google_event_ids = [
                occurrence.google_calendar_event_id
                for occurrence in occurrences
                if occurrence.google_calendar_event_id
            ]
            try:
                root_event_id = event_to_delete.pk
                deleted_count = delete_event_with_tombstones(
                    event_to_delete,
                    occurrences,
                )
                success_count += deleted_count
                logger.info(
                    "データベースからの削除成功: root_id=%s count=%s",
                    root_event_id,
                    deleted_count,
                )
            except Exception:
                logger.exception(
                    "イベントの削除に失敗: ID=%s",
                    event_to_delete.id,
                )
                error_count += 1
                continue

            google_error_count += self._delete_google_calendar_events(
                google_event_ids
            )

        if success_count > 0:
            if delete_subsequent:
                messages.success(request, f"{success_count}件のイベントを削除しました。")
            else:
                messages.success(request, "イベントを削除しました。")

        if error_count > 0:
            messages.error(request, f"{error_count}件のイベントの削除中にエラーが発生しました。")

        if google_error_count > 0:
            messages.warning(
                request,
                f"{google_error_count}件のGoogleカレンダー削除に失敗しました。"
                "後続の同期で再反映します。",
            )

        return redirect('event:my_list')

    @staticmethod
    def _get_cascade_lock_message(request, occurrences) -> str:
        """削除連鎖にVketロック中の開催回があればメッセージを返す。"""
        if request.user.is_superuser or request.user.is_staff:
            return ''

        from vket.services import get_vket_lock_info

        for occurrence in occurrences:
            locked, message = get_vket_lock_info(occurrence)
            if locked:
                return message
        return ''

    @staticmethod
    def _delete_google_calendar_events(event_ids) -> int:
        """Google Calendarの削除失敗数を返す。"""
        error_count = 0
        for event_id in event_ids:
            try:
                calendar_service = GoogleCalendarService(
                    calendar_id=GOOGLE_CALENDAR_ID,
                    credentials_path=GOOGLE_CALENDAR_CREDENTIALS,
                )
                logger.info(
                    "Googleカレンダーからの削除を試行: Event ID=%s",
                    event_id,
                )
                calendar_service.delete_event(event_id)
            except Exception:
                logger.exception(
                    "Googleカレンダーからの削除失敗: Event ID=%s",
                    event_id,
                )
                error_count += 1
        return error_count


class EventDetailCreateView(LoginRequiredMixin, AuthenticatedForbiddenMixin, CreateView):
    model = EventDetail
    form_class = EventDetailForm
    template_name = 'event/detail_form.html'

    def dispatch(self, request, *args, **kwargs):
        self.event = get_object_or_404(Event, pk=kwargs['event_pk'])
        return super().dispatch(request, *args, **kwargs)

    def test_func(self):
        # イベント詳細は、所属コミュニティの管理者（owner/staff）またはsuperuserのみ作成可
        return self.request.user.is_superuser or self.event.community.can_edit(self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['request'] = self.request
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event'] = self.event
        # イベントが開催前かどうかを判定
        from datetime import date
        context['is_before_event'] = self.event.date > date.today()
        return context

    def form_valid(self, form):
        form.instance.event = self.event
        response = super().form_valid(form)

        # チェックボックスがONで、LTタイプで、PDFまたは動画がセットされている場合は自動生成
        generate_blog_flag = form.cleaned_data.get('generate_blog_article', False)
        if (generate_blog_flag and
            form.instance.detail_type == 'LT' and
                (form.instance.slide_file or form.instance.youtube_url)):
            try:
                from event.services.content_generation_service import generate_blog as generate_blog_func
                blog_output = generate_blog_func(form.instance, model=GEMINI_MODEL)
                # 空でないことを確認
                if apply_blog_output_to_event_detail(form.instance, blog_output):
                    form.instance.save()
                    messages.success(self.request, "記事を自動生成しました。")
                    logger.info(f"記事を自動生成しました: {form.instance.id}")
                else:
                    logger.warning(f"記事の自動生成に失敗しました（空の結果）: {form.instance.id}")
                    messages.warning(self.request, "記事の自動生成に失敗しました。")
            except Exception:
                # silent failure: 記事生成失敗はユーザー操作 (詳細作成) を止めない設計。
                # Sentry で連発検知できるよう is_silent=True を付与する。
                logger.exception(
                    "silent_failure",
                    extra={
                        "event_type": "blog_generation_failed_on_create",
                        "event_detail_id": form.instance.id,
                        "is_silent": True,
                    },
                )
                messages.error(self.request, "記事の自動生成中にエラーが発生しました")

        return response

    def get_success_url(self):
        return reverse_lazy('event:detail', kwargs={'pk': self.object.pk})


class EventDetailUpdateView(LoginRequiredMixin, AuthenticatedForbiddenMixin, UpdateView):
    model = EventDetail
    form_class = EventDetailForm
    template_name = 'event/detail_form.html'

    def test_func(self):
        event_detail = self.get_object()
        # 発表者本人は自分の承認済みLTのみ更新可（発表者フローを保ちつつ権限範囲を限定する）。
        return can_manage_event_detail(self.request.user, event_detail)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['request'] = self.request
        return kwargs

    def handle_no_permission(self):
        """認証済みだが権限がないユーザーはイベント詳細ページにリダイレクトする."""
        if self.request.user.is_authenticated:
            return redirect('event:detail', pk=self.get_object().pk)
        return super().handle_no_permission()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event'] = self.object.event
        # イベントが開催前かどうかを判定
        from datetime import date
        context['is_before_event'] = self.object.event.date > date.today()
        return context

    def form_valid(self, form):
        response = super().form_valid(form)

        # チェックボックスがONで、LTタイプで、PDFまたは動画がセットされている場合は自動生成
        generate_blog_flag = form.cleaned_data.get('generate_blog_article', False)
        if (generate_blog_flag and
            form.instance.detail_type == 'LT' and
                (form.instance.slide_file or form.instance.youtube_url)):
            try:
                blog_output = generate_blog(form.instance, model=GEMINI_MODEL)
                # 空でないことを確認
                if apply_blog_output_to_event_detail(form.instance, blog_output):
                    form.instance.save()
                    messages.success(self.request, "記事を自動生成しました。")
                    logger.info(f"記事を自動生成しました: {form.instance.id}")
                else:
                    logger.warning(f"記事の自動生成に失敗しました（空の結果）: {form.instance.id}")
                    messages.warning(self.request, "記事の自動生成に失敗しました。")
            except Exception:
                # silent failure: 更新操作で記事生成が失敗してもフォーム送信は成功させる。
                # Sentry/監視で同種エラー連発を検知できるよう is_silent=True を付与。
                logger.exception(
                    "silent_failure",
                    extra={
                        "event_type": "blog_generation_failed_on_update",
                        "event_detail_id": form.instance.id,
                        "is_silent": True,
                    },
                )
                messages.error(self.request, "記事の自動生成中にエラーが発生しました")

        return response

    def get_success_url(self):
        return reverse_lazy('event:detail', kwargs={'pk': self.object.pk})

    def is_valid_request(self, request, pk):
        pass


class EventDetailDeleteView(LoginRequiredMixin, AuthenticatedForbiddenMixin, DeleteView):
    model = EventDetail
    template_name = 'event/detail_confirm_delete.html'

    def test_func(self):
        event_detail = self.get_object()
        # イベント詳細は、所属コミュニティの管理者（owner/staff）またはsuperuserのみ削除可
        return self.request.user.is_superuser or event_detail.event.community.can_edit(self.request.user)

    def post(self, request, *args, **kwargs):
        # Vketコラボ期間中は運営調整済みの登壇情報を主催者が誤って消さないよう、EventDetail削除をブロックする
        event_detail = self.get_object()
        if not (request.user.is_superuser or request.user.is_staff):
            from vket.services import get_vket_lock_info
            locked, message = get_vket_lock_info(event_detail.event)
            if locked:
                messages.error(request, message)
                return redirect('event:detail', pk=event_detail.pk)
        return super().post(request, *args, **kwargs)

    def get_success_url(self):
        return reverse_lazy('event:my_list')
