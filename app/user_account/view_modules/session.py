"""ログイン・ログアウト・登録に関する view 群."""

import logging

from django.contrib import messages
from django.contrib.auth.hashers import make_password
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView, PasswordChangeView, RedirectURLMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import FormView, TemplateView

from allauth.account import app_settings
# 登録済みアドレスへの応答は allauth の SignupForm と同じ内部フローを使う（65.18.0 に固定）。
from allauth.account.internal.flows.email_verification import add_email_verification_sent_message
from allauth.account.internal.flows.signup import prevent_enumeration
from allauth.account.models import EmailAddress
from allauth.account.utils import complete_signup, perform_login
from allauth.core import ratelimit
from django.db import IntegrityError, transaction

from user_account.adapters import ConfirmationEmailDeliveryError
from user_account.discord_oauth import is_discord_oauth_available
from user_account.email_ownership import is_email_in_use
from user_account.forms import (
    BootstrapAuthenticationForm,
    BootstrapPasswordChangeForm,
    LocalSignupForm,
)
from user_account.login_redirect import get_default_login_redirect_url

logger = logging.getLogger(__name__)

SIGNUP_RATE_LIMIT_ACTION = 'signup'
CONFIRM_EMAIL_RATE_LIMIT_ACTION = 'confirm_email'
SIGNUP_MAIL_FAILURE_MESSAGE = (
    '登録は完了しました。確認メールの送信に失敗したため、ログイン画面から再送してください。'
)


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
        session_expiry = app_settings.SESSION_COOKIE_AGE if remember else 0
        self.request.session.set_expiry(session_expiry)
        response = perform_login(
            self.request,
            form.get_user(),
            email_verification=app_settings.EmailVerificationMethod.MANDATORY,
            redirect_url=self.get_redirect_url() or get_default_login_redirect_url(form.get_user()),
            email=form.cleaned_data['username'].lower(),
        )
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

    def post(self, request, *args, **kwargs):
        # Discord 登録のみの構成ではフォームを検証しない。clean_email の重複エラーで
        # メールの登録有無が外から判別できてしまうため、検証前に一律で弾く（#609）。
        if self.discord_oauth_enabled:
            return redirect('account:register')
        # 登録 POST を IP 単位で制限する（入力された email に依らないので登録有無は漏れない）。
        rate_limited_response = ratelimit.consume_or_429(request, action=SIGNUP_RATE_LIMIT_ACTION)
        if rate_limited_response:
            return rate_limited_response
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        """登録済みかどうかに関わらず、同じリダイレクト・同じ表示で応答する。"""
        email = form.cleaned_data['email']
        # allauth は宛先 email ごとの送信制限に当たると、送信も「送信しました」の表示も省く。
        # 表示の有無から登録有無を推測されないよう、制限中も同じ表示を出す。
        mail_throttled = not ratelimit.consume(
            self.request,
            action=CONFIRM_EMAIL_RATE_LIMIT_ACTION,
            key=email,
            dry_run=True,
        )
        try:
            if form.account_already_exists:
                response = self._respond_to_registered_email(form, email)
            else:
                response = self._sign_up_new_user(form, email)
        except ConfirmationEmailDeliveryError as exc:
            logger.error(
                'Failed to send signup mail: exception_type=%s',
                type(exc).__name__,
                exc_info=True,
            )
            messages.warning(self.request, SIGNUP_MAIL_FAILURE_MESSAGE)
            return redirect('account:login')
        if mail_throttled:
            add_email_verification_sent_message(self.request, email, signup=True)
        return response

    def _respond_to_registered_email(self, form, email):
        """アカウントは作らず、登録済みの案内メールを送って新規登録と同じ応答を返す。"""
        # 新規登録はパスワードをハッシュ化する分だけ遅い。処理時間の差で登録有無を推測されないよう、
        # 保存しないパスワードも一度ハッシュ化する（Django の ModelBackend と同じ手法）。
        make_password(form.cleaned_data['password1'])
        return prevent_enumeration(self.request, email=email)

    def _sign_up_new_user(self, form, email):
        try:
            with transaction.atomic():
                user = form.save()
                # allauth の setup_user_email は他ユーザーの確認待ちの行と同じアドレスを黙って捨てるため、
                # 登録アドレスを未確認の primary として直接作る（重複は CustomUser.email の unique が防ぐ）。
                EmailAddress.objects.create(
                    user=user,
                    email=user.email,
                    primary=True,
                    verified=False,
                )
        except IntegrityError:
            # 重複判定の後に別リクエストが同じアドレスを登録した競合。500 にせず登録済みと同じ応答にする。
            if not is_email_in_use(email):
                raise
            return self._respond_to_registered_email(form, email)
        # Send only after the user and its unverified primary address commit.
        # A mail delivery failure deliberately leaves this state for login resend.
        redirect_url = self.get_redirect_url() or get_default_login_redirect_url(user)
        return complete_signup(
            self.request,
            user,
            app_settings.EmailVerificationMethod.MANDATORY,
            redirect_url,
        )


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
