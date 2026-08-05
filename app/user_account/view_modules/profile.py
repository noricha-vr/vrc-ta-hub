"""プロフィールと設定画面に関する view 群."""

import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.utils.safestring import mark_safe
from django.views.generic import TemplateView, UpdateView

from allauth.account.models import EmailAddress

from analytics import services as analytics_services
from community.models import CommunityMember
from user_account.forms import CustomUserChangeForm

logger = logging.getLogger(__name__)


class UserNameChangeView(LoginRequiredMixin, UpdateView):
    form_class = CustomUserChangeForm
    success_url = reverse_lazy('account:settings')
    template_name = 'account/user_name_change.html'

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        messages.success(self.request, 'ユーザー名が変更されました。')
        return super().form_valid(form)


class UserUpdateView(LoginRequiredMixin, UpdateView):
    form_class = CustomUserChangeForm
    success_url = reverse_lazy('account:settings')
    template_name = 'account/user_update.html'

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        new_email = form.cleaned_data['email']
        old_email = form.original_email
        response = super().form_valid(form)
        if new_email != old_email:
            try:
                EmailAddress.objects.add_new_email(self.request, self.request.user, new_email)
            except Exception as exc:
                # The current email remains authoritative. allauth may have
                # committed a pending row before the mail backend failed, so a
                # later submission of the same address acts as a safe resend.
                logger.error(
                    'Failed to send email-change confirmation: '
                    'user_id=%s exception_type=%s',
                    self.request.user.pk,
                    type(exc).__name__,
                )
                messages.warning(
                    self.request,
                    '確認メールを送信できませんでした。現在のメールアドレスは変更されていません。時間をおいて再度お試しください。',
                )
            else:
                messages.success(
                    self.request,
                    '確認メールを新しいメールアドレスに送信しました。確認完了まで現在のメールアドレスを使用します。',
                )
        else:
            messages.success(self.request, 'ユーザー情報が更新されました。')
        return response


class SettingsView(LoginRequiredMixin, TemplateView):
    template_name = 'account/settings.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = self.request.user.community_memberships.filter(
            role=CommunityMember.Role.OWNER,
        ).select_related('community').first()
        context['community'] = membership.community if membership else None

        # アクセス可能な全 community を合算したアクセス解析（superuser は全 community）。
        # community_ids 指定だけで content_type/object_id は指定しない（全体集計）。
        community_ids = analytics_services.accessible_community_ids(self.request.user)
        context['daily_series'] = analytics_services.get_daily_series(community_ids)
        context['source_breakdown'] = analytics_services.get_source_breakdown(community_ids)

        if context['community'] and not context['community'].is_accepted:
            message = mark_safe(
                'この集会は現在承認待ちです。Hub運営スタッフに承認されると公開されるようになります。'
                'Discord <a href="https://discord.gg/6jCkUUb9VN" target="_blank" rel="noopener noreferrer" class="alert-link">技術・学術系Hub</a>にご参加ください。'
            )
            messages.warning(self.request, message)
        return context
