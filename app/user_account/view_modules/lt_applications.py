"""LT申請の一覧と編集に関する view 群."""

import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.urls import reverse
from django.views.generic import ListView, UpdateView

from event.forms import LTApplicationEditForm
from event.models import EventDetail

logger = logging.getLogger(__name__)

UPDATED_MESSAGE = '発表申請情報を更新しました。'
ARTICLE_QUEUED_MESSAGE = '発表申請情報を更新しました。記事は自動で作成し、できあがったらメールでお知らせします。'


def _owned_lt_condition(user) -> Q:
    """ユーザーが自分のLTとして扱える条件。

    リマインドメールの受信者選定（event.material_upload_reminders.get_material_reminder_recipient）
    と整合させる: applicant 本人、または applicant 未設定の Vket 由来発表の申請者本人。
    一覧と編集で条件が食い違うと「一覧に出るのに編集で404」の乖離バグになるため必ず共用する。
    """
    return Q(applicant=user) | Q(
        applicant__isnull=True,
        vket_presentations__participation__applied_by=user,
    )


class LTApplicationListView(LoginRequiredMixin, ListView):
    """LT申請一覧ページ."""

    template_name = 'account/lt_application_list.html'
    context_object_name = 'applications'

    def get_queryset(self):
        return EventDetail.objects.filter(
            _owned_lt_condition(self.request.user),
            detail_type='LT',
        ).select_related('event', 'event__community').distinct().order_by('-event__date', '-created_at')


class LTApplicationEditView(LoginRequiredMixin, UpdateView):
    """LT申請編集ページ."""

    template_name = 'account/lt_application_edit.html'

    def get_form_class(self):
        return LTApplicationEditForm

    def get_queryset(self):
        return EventDetail.objects.filter(
            _owned_lt_condition(self.request.user),
            detail_type='LT',
        ).exclude(status='rejected').select_related('event', 'event__community').distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event'] = self.object.event
        context['community'] = self.object.event.community
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        instance = form.instance

        # 判定は保存後の値で行う（同じ送信で記事化を NG / OK に変えた時も拾う）
        if instance.can_auto_generate_article:
            # 記事化 OK の発表はキュー（Cloud Scheduler）が作る。ここでも作ると二重になる
            queued = instance.article_generation_requested_at is not None
            messages.success(self.request, ARTICLE_QUEUED_MESSAGE if queued else UPDATED_MESSAGE)
        elif self._should_generate_now(form):
            self._generate_now(instance)
        else:
            messages.success(self.request, UPDATED_MESSAGE)

        return response

    @staticmethod
    def _should_generate_now(form) -> bool:
        """チェックボックスで記事の生成を頼まれ、動画か PDF がある（記事化 NG を除く）。"""
        instance = form.instance
        return bool(
            form.cleaned_data.get('generate_blog_article', False)
            and not instance.is_article_ng
            and (instance.slide_file or instance.youtube_url)
        )

    def _generate_now(self, instance):
        """保存と同時に記事を作る（記事化が未回答の発表のこれまでの動き）。

        保存は記事の列だけを書き、生成を待つ間に記事化が NG になっていたら書かない
        （save_generated_article）。instance をそのまま save() すると古い値で戻すため。
        """
        try:
            from django.conf import settings as django_settings
            from event.services.content_generation_service import (
                REFUSED,
                SAVED,
                generate_blog,
                save_generated_article,
            )

            blog_output = generate_blog(instance, model=django_settings.GEMINI_MODEL)
            outcome = save_generated_article(instance, blog_output)
            if outcome == SAVED:
                messages.success(self.request, "発表申請情報を更新し、記事を自動生成しました。")
                logger.info(f"記事を自動生成しました: {instance.id}")
            elif outcome == REFUSED:
                messages.warning(self.request, "発表申請情報を更新しました。記事化が NG のため、記事は保存しませんでした。")
            else:
                logger.warning(f"記事の自動生成に失敗しました（空の結果）: {instance.id}")
                messages.warning(self.request, "発表申請情報を更新しましたが、記事の自動生成に失敗しました。")
        except Exception as e:
            logger.error(f"記事の自動生成中にエラーが発生しました: {str(e)}")
            messages.error(self.request, "発表申請情報を更新しましたが、記事の自動生成中にエラーが発生しました。")

    def get_success_url(self):
        return reverse('account:lt_application_list')
