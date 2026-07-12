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


class LTApplicationListView(LoginRequiredMixin, ListView):
    """LT申請一覧ページ."""

    template_name = 'account/lt_application_list.html'
    context_object_name = 'applications'

    def get_queryset(self):
        return EventDetail.objects.filter(
            Q(applicant=self.request.user)
            | Q(
                applicant__isnull=True,
                vket_presentations__participation__applied_by=self.request.user,
            ),
            detail_type='LT',
        ).select_related('event', 'event__community').distinct().order_by('-event__date', '-created_at')


class LTApplicationEditView(LoginRequiredMixin, UpdateView):
    """LT申請編集ページ."""

    template_name = 'account/lt_application_edit.html'

    def get_form_class(self):
        return LTApplicationEditForm

    def get_queryset(self):
        return EventDetail.objects.filter(
            Q(applicant=self.request.user)
            | Q(
                applicant__isnull=True,
                vket_presentations__participation__applied_by=self.request.user,
            ),
            detail_type='LT',
        ).exclude(status='rejected').select_related('event', 'event__community').distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event'] = self.object.event
        context['community'] = self.object.event.community
        return context

    def form_valid(self, form):
        response = super().form_valid(form)

        generate_blog_flag = form.cleaned_data.get('generate_blog_article', False)
        if (
            generate_blog_flag
            and (form.instance.slide_file or form.instance.youtube_url)
        ):
            try:
                from django.conf import settings as django_settings
                from event.services.content_generation_service import apply_blog_output_to_event_detail, generate_blog

                blog_output = generate_blog(form.instance, model=django_settings.GEMINI_MODEL)
                if apply_blog_output_to_event_detail(form.instance, blog_output):
                    form.instance.save()
                    messages.success(self.request, "発表申請情報を更新し、記事を自動生成しました。")
                    logger.info(f"記事を自動生成しました: {form.instance.id}")
                else:
                    logger.warning(f"記事の自動生成に失敗しました（空の結果）: {form.instance.id}")
                    messages.warning(self.request, "発表申請情報を更新しましたが、記事の自動生成に失敗しました。")
            except Exception as e:
                logger.error(f"記事の自動生成中にエラーが発生しました: {str(e)}")
                messages.error(self.request, "発表申請情報を更新しましたが、記事の自動生成中にエラーが発生しました。")
        else:
            messages.success(self.request, '発表申請情報を更新しました。')

        return response

    def get_success_url(self):
        return reverse('account:lt_application_list')
