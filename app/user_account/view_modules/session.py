"""ログイン・ログアウト・登録に関する view 群."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView, PasswordChangeView
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import FormView, TemplateView

from user_account.discord_oauth import is_discord_oauth_available
from user_account.forms import (
    BootstrapAuthenticationForm,
    BootstrapPasswordChangeForm,
    LocalSignupForm,
)


@method_decorator(ensure_csrf_cookie, name='dispatch')
class CustomLoginView(LoginView):
    template_name = 'account/login.html'
    form_class = BootstrapAuthenticationForm

    def dispatch(self, request, *args, **kwargs):
        """認証済みユーザーをイベント管理ページへ移動させる."""
        if request.user.is_authenticated:
            return redirect('event:my_list')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        """ログイン成功時に remember 設定からセッション期限を決める."""
        remember = form.cleaned_data.get('remember')
        response = super().form_valid(form)
        if not remember:
            self.request.session.set_expiry(0)
        return response

    def get_success_url(self):
        messages.info(self.request, 'ログインしました。')
        redirect_url = self.get_redirect_url()
        if redirect_url:
            return redirect_url
        return settings.LOGIN_REDIRECT_URL

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['discord_oauth_enabled'] = is_discord_oauth_available(self.request)
        return context


class CustomLogoutView(LogoutView):
    next_page = reverse_lazy('account:login')

    def dispatch(self, request, *args, **kwargs):
        messages.info(request, 'ログアウトしました。')
        return super().dispatch(request, *args, **kwargs)


class RegisterView(FormView):
    """新規登録ページ."""

    template_name = 'account/register.html'
    form_class = LocalSignupForm
    success_url = reverse_lazy('account:login')

    def dispatch(self, request, *args, **kwargs):
        self.discord_oauth_enabled = is_discord_oauth_available(request)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['discord_oauth_enabled'] = self.discord_oauth_enabled
        return context

    def form_valid(self, form):
        if self.discord_oauth_enabled:
            return redirect('account:register')

        form.save()
        messages.success(self.request, 'アカウントを作成しました。ログインしてください。')
        return super().form_valid(form)


class CustomPasswordChangeView(LoginRequiredMixin, PasswordChangeView):
    success_url = reverse_lazy('account:settings')
    template_name = 'account/password_change.html'
    form_class = BootstrapPasswordChangeForm

    def form_valid(self, form):
        messages.success(self.request, 'パスワードが変更されました。')
        return super().form_valid(form)


class DiscordRequiredView(LoginRequiredMixin, TemplateView):
    """Discord連携必須ページ."""

    template_name = 'account/discord_required.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['discord_oauth_enabled'] = is_discord_oauth_available(self.request)
        return context
