from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.contrib.auth.views import PasswordChangeView
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.utils.html import strip_tags
from django.utils.safestring import mark_safe
from django.views.generic import CreateView, TemplateView
from django.views.generic import UpdateView

from community.models import Community
from .forms import CustomUserChangeForm
from .forms import CustomUserCreationForm, BootstrapAuthenticationForm, BootstrapPasswordChangeForm


class CustomLoginView(LoginView):
    template_name = 'account/login.html'
    success_url = reverse_lazy('account:settings')  # ログイン成功後のリダイレクト先
    form_class = BootstrapAuthenticationForm

    def get_success_url(self):
        messages.info(self.request, 'ログインしました。')
        return self.success_url


class CustomLogoutView(LogoutView):
    next_page = reverse_lazy('account:login')  # ログアウト後のリダイレクト先

    def dispatch(self, request, *args, **kwargs):
        messages.info(request, 'ログアウトしました。')
        return super().dispatch(request, *args, **kwargs)


class CustomUserCreateView(CreateView):
    form_class = CustomUserCreationForm
    template_name = 'account/register.html'
    success_url = reverse_lazy('account:login')

    def form_valid(self, form):
        response = super().form_valid(form)
        user = form.instance

        # メール本文の作成
        context = {
            'user': user,
            'login_url': self.request.build_absolute_uri(reverse_lazy('account:login')),
            'discord_url': 'https://discord.gg/6jCkUUb9VN'
        }
        html_message = render_to_string('account/email/welcome.html', context)
        plain_message = strip_tags(html_message)

        # メール送信
        send_mail(
            subject='VRC技術学術系Hub 登録完了のお知らせ',
            message=plain_message,
            from_email=None,  # settings.pyのDEFAULT_FROM_EMAILが使用されます
            recipient_list=[user.email],
            html_message=html_message,
        )

        messages.success(self.request, 'ユーザー登録が完了しました。集会は承認後に公開されます。')
        message = 'Discordサーバー「<a href="https://discord.gg/6jCkUUb9VN">技術・学術系イベントHub</a>」にご参加ください。'
        messages.warning(self.request, mark_safe(message))
        return response


class UserNameChangeView(LoginRequiredMixin, UpdateView):
    form_class = CustomUserChangeForm
    success_url = reverse_lazy('account:settings')
    template_name = 'account/user_name_change.html'

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        messages.success(self.request, 'ユーザー名が変更されました。')
        return super().form_valid(form)


class CustomPasswordChangeView(LoginRequiredMixin, PasswordChangeView):
    success_url = reverse_lazy('account:settings')
    template_name = 'account/password_change.html'
    form_class = BootstrapPasswordChangeForm

    def form_valid(self, form):
        messages.success(self.request, 'パスワードが変更されました。')
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
        context['community'] = Community.objects.filter(custom_user=self.request.user).first()
        # 承認されていない場合はメッセージを追加
        if context['community'] and not context['community'].is_accepted:
            message = 'この集会は現在承認待ちです。既に公開されている技術・学術系集会に承認されると公開されるようになります。'
            messages.warning(self.request, message)
        return context

# for user in CustomUser.objects.all():
#     password = secrets.token_hex(12)  # ランダムな16文字のパスワードを生成
#     user.set_password(password)
#     user.save()
#     print(user.user_name, password)
