"""プロフィールと設定画面に関する view 群."""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.utils.safestring import mark_safe
from django.views.generic import TemplateView, UpdateView

from community.models import CommunityMember
from user_account.forms import CustomUserChangeForm


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
        messages.success(self.request, 'ユーザー情報が更新されました。')
        return super().form_valid(form)


class SettingsView(LoginRequiredMixin, TemplateView):
    template_name = 'account/settings.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        membership = self.request.user.community_memberships.filter(
            role=CommunityMember.Role.OWNER,
        ).select_related('community').first()
        context['community'] = membership.community if membership else None
        if context['community'] and not context['community'].is_accepted:
            message = mark_safe(
                'この集会は現在承認待ちです。Hub運営スタッフに承認されると公開されるようになります。'
                'Discord <a href="https://discord.gg/6jCkUUb9VN" target="_blank" rel="noopener noreferrer" class="alert-link">技術・学術系Hub</a>にご参加ください。'
            )
            messages.warning(self.request, message)
        return context
