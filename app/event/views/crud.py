import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, UpdateView, DeleteView

from event.forms import EventDetailForm
from event.libs import apply_blog_output_to_event_detail, generate_blog
from event.models import Event, EventDetail
from event.views.helpers import can_manage_event_detail
from website.settings import GOOGLE_CALENDAR_CREDENTIALS, GOOGLE_CALENDAR_ID, GEMINI_MODEL
from event.google_calendar import GoogleCalendarService

logger = logging.getLogger(__name__)


class EventDeleteView(LoginRequiredMixin, DeleteView):
    model = Event
    success_url = reverse_lazy('event:my_list')

    def post(self, request, *args, **kwargs):
        event = self.get_object()

        # Vketコラボ期間中のイベント削除をブロック（参照: PR #138）
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

        # Vketコラボ期間中のイベントを削除対象から除外（参照: PR #138）
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

        for event_to_delete in events_to_delete:
            try:
                # Googleカレンダーからイベントを削除
                if event_to_delete.google_calendar_event_id:
                    try:
                        calendar_service = GoogleCalendarService(
                            calendar_id=GOOGLE_CALENDAR_ID,
                            credentials_path=GOOGLE_CALENDAR_CREDENTIALS
                        )
                        logger.info(
                            f"Googleカレンダーからの削除を試行: Event ID={event_to_delete.google_calendar_event_id}")
                        calendar_service.delete_event(event_to_delete.google_calendar_event_id)
                        logger.info(
                            f"Googleカレンダーからの削除成功: Event ID={event_to_delete.google_calendar_event_id}")
                    except Exception as e:
                        logger.error(
                            f"Googleカレンダーからの削除失敗: Event ID={event_to_delete.google_calendar_event_id}, エラー={str(e)}")
                        error_count += 1
                        continue

                # データベースからイベントを削除
                event_to_delete.delete()
                success_count += 1
                logger.info(f"データベースからの削除成功: ID={event_to_delete.id}")

            except Exception as e:
                logger.error(f"イベントの削除に失敗: ID={event_to_delete.id}, エラー={str(e)}")
                error_count += 1

        if success_count > 0:
            if delete_subsequent:
                messages.success(request, f"{success_count}件のイベントを削除しました。")
            else:
                messages.success(request, "イベントを削除しました。")

        if error_count > 0:
            messages.error(request, f"{error_count}件のイベントの削除中にエラーが発生しました。")

        return redirect('event:my_list')


class EventDetailCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
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
                from event.libs import generate_blog as generate_blog_func
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
                logger.exception("記事の自動生成中にエラーが発生しました")
                messages.error(self.request, "記事の自動生成中にエラーが発生しました")

        return response

    def get_success_url(self):
        return reverse_lazy('event:detail', kwargs={'pk': self.object.pk})


class EventDetailUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = EventDetail
    form_class = EventDetailForm
    template_name = 'event/detail_form.html'

    def test_func(self):
        event_detail = self.get_object()
        # 発表者本人は自分の承認済みLTのみ更新可。参照: PR #116（発表者フローを保ちつつ権限範囲を限定するため）
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
                logger.exception("記事の自動生成中にエラーが発生しました")
                messages.error(self.request, "記事の自動生成中にエラーが発生しました")

        return response

    def get_success_url(self):
        return reverse_lazy('event:detail', kwargs={'pk': self.object.pk})

    def is_valid_request(self, request, pk):
        pass


class EventDetailDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = EventDetail
    template_name = 'event/detail_confirm_delete.html'

    def test_func(self):
        event_detail = self.get_object()
        # イベント詳細は、所属コミュニティの管理者（owner/staff）またはsuperuserのみ削除可
        return self.request.user.is_superuser or event_detail.event.community.can_edit(self.request.user)

    def post(self, request, *args, **kwargs):
        # Vketコラボ期間中のEventDetail削除をブロック（参照: PR #138）
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

