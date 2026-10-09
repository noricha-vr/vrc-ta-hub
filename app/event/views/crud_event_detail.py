import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, UpdateView, DeleteView

from event.forms import EventDetailForm
from event.services.content_generation_service import REFUSED, SAVED, generate_blog, save_generated_article
from event.models import Event, EventDetail
from event.views.helpers import can_manage_event_detail
from ta_hub.access_mixins import AuthenticatedForbiddenMixin
from website.settings import GEMINI_MODEL

logger = logging.getLogger(__name__)

ARTICLE_QUEUED_MESSAGE = "記事は自動で作成します。できあがったら発表者にメールでお知らせします。"
ARTICLE_REFUSED_MESSAGE = "生成している間に発表者が記事化を NG にしたため、記事は保存しませんでした。"


def _save_and_report(request, event_detail: EventDetail, blog_output) -> None:
    """生成結果を保存し、結果をメッセージで知らせる。

    保存は記事の列だけを書き、生成を待つ間に記事化が NG になっていたら書かない
    （save_generated_article）。フォームのインスタンスをそのまま save() すると古い値で戻すため。
    """
    outcome = save_generated_article(event_detail, blog_output)
    if outcome == SAVED:
        messages.success(request, "記事を自動生成しました。")
        logger.info(f"記事を自動生成しました: {event_detail.id}")
    elif outcome == REFUSED:
        messages.error(request, ARTICLE_REFUSED_MESSAGE)
    else:
        logger.warning(f"記事の自動生成に失敗しました（空の結果）: {event_detail.id}")
        messages.warning(request, "記事の自動生成に失敗しました。")


def _should_generate_on_save(form) -> bool:
    """保存と同時に記事を作るか（チェックボックスが ON の発表で、動画か PDF がある時）。

    記事化 NG の発表はチェックボックスを出していなくてもサーバ側で断る。チェックボックスで明示的に
    頼まれた時は記事化 OK の発表でもここで作る（記事化 OK の発表にはふつうチェックボックスを出さず、
    キュー（Cloud Scheduler）が作る）。保存すると生成待ちの印は外れ、キューとは重ならない。
    判定は保存後の値で行う。
    """
    instance = form.instance
    return bool(
        form.cleaned_data.get('generate_blog_article', False)
        and instance.detail_type == 'LT'
        and not instance.is_article_ng
        and (instance.slide_file or instance.youtube_url)
    )


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
        if _should_generate_on_save(form):
            try:
                from event.services.content_generation_service import generate_blog as generate_blog_func
                blog_output = generate_blog_func(form.instance, model=GEMINI_MODEL)
                _save_and_report(self.request, form.instance, blog_output)
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
        if _should_generate_on_save(form):
            try:
                blog_output = generate_blog(form.instance, model=GEMINI_MODEL)
                _save_and_report(self.request, form.instance, blog_output)
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
        elif form.instance.can_auto_generate_article and form.instance.article_generation_requested_at:
            # 記事化 OK の発表は自動生成のキューが作る（保存時には作らない）
            messages.info(self.request, ARTICLE_QUEUED_MESSAGE)

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
