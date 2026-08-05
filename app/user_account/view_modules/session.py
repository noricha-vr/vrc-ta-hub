"""ログイン・ログアウト・登録に関する view 群."""

import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView, PasswordChangeView, RedirectURLMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import FormView, TemplateView

from allauth.account import app_settings
from allauth.account.utils import complete_signup, perform_login, setup_user_email
from django.db import transaction

from user_account.adapters import ConfirmationEmailDeliveryError
from user_account.discord_oauth import is_discord_oauth_available
from user_account.forms import (
    BootstrapAuthenticationForm,
    BootstrapPasswordChangeForm,
    LocalSignupForm,
)
from user_account.login_redirect import get_default_login_redirect_url

logger = logging.getLogger(__name__)


@method_decorator(ensure_csrf_cookie, name='dispatch')
class CustomLoginView(LoginView):
    template_name = 'account/login.html'
    form_class = BootstrapAuthenticationForm

    def dispatch(self, request, *args, **kwargs):
        """認証済みユーザーを所属状況に応じた既定ページへ移動させる."""
        if request.user.is_authenticated:
            return redirect(get_default_login_redirect_url(request.user))
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        """allauth の確認ステージを通じてログインする."""
        remember = form.cleaned_data.get('remember')
        response = perform_login(
            self.request,
            form.get_user(),
            email_verification=app_settings.EmailVerificationMethod.MANDATORY,
            redirect_url=self.get_redirect_url() or get_default_login_redirect_url(form.get_user()),
            email=form.cleaned_data['username'].lower(),
        )
        if self.request.user.is_authenticated and not remember:
            self.request.session.set_expiry(0)
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['discord_oauth_enabled'] = is_discord_oauth_available(self.request)
        context['redirect_field_name'] = self.redirect_field_name
        context['redirect_field_value'] = self.get_redirect_url()
        return context


class CustomLogoutView(LogoutView):
    next_page = reverse_lazy('account:login')

    def dispatch(self, request, *args, **kwargs):
        messages.info(request, 'ログアウトしました。')
        return super().dispatch(request, *args, **kwargs)


class RegisterView(RedirectURLMixin, FormView):
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
        context['redirect_field_name'] = self.redirect_field_name
        context['redirect_field_value'] = self.get_redirect_url()
        return context

    def form_valid(self, form):
        if self.discord_oauth_enabled:
            return redirect('account:register')

        with transaction.atomic():
            user = form.save()
            setup_user_email(self.request, user, [])
        # Send only after the user and its unverified primary address commit.
        # A mail delivery failure deliberately leaves this state for login resend.
        redirect_url = self.get_redirect_url() or get_default_login_redirect_url(user)
        try:
            return complete_signup(
                self.request,
                user,
                app_settings.EmailVerificationMethod.MANDATORY,
                redirect_url,
            )
        except ConfirmationEmailDeliveryError as exc:
            logger.error(
                'Failed to send signup confirmation: user_id=%s exception_type=%s',
                user.pk,
                type(exc).__name__,
                exc_info=True,
            )
            messages.warning(
                self.request,
                '登録は完了しました。確認メールの送信に失敗したため、ログイン画面から再送してください。',
            )
            return redirect('account:login')


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
