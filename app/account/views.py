from django.contrib.auth.views import LoginView, LogoutView
from django.urls import reverse_lazy
from django.views.generic import CreateView, TemplateView

from community.models import Community
from .forms import CustomUserCreationForm, BootstrapAuthenticationForm, BootstrapPasswordChangeForm


class CustomLoginView(LoginView):
    template_name = 'account/login.html'
    success_url = reverse_lazy('account:settings')  # ログイン成功後のリダイレクト先
    form_class = BootstrapAuthenticationForm

    def get_success_url(self):
        messages.success(self.request, 'ログインしました。')
        return self.success_url


class CustomLogoutView(LogoutView):
    next_page = reverse_lazy('account:login')  # ログアウト後のリダイレクト先


class CustomUserCreateView(CreateView):
    form_class = CustomUserCreationForm
    template_name = 'account/register.html'
    success_url = reverse_lazy('account:login')

    def form_valid(self, form):
        messages.success(self.request, 'ユーザー登録が完了しました。集会は承認後に公開されます。')
        message = 'Discordサーバー「<a href="https://discord.gg/6jCkUUb9VN">技術・学術系イベントHub</a>」にご参加ください。'
        messages.warning(self.request, message)
        return super().form_valid(form)


from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import UpdateView
from .forms import CustomUserChangeForm


class UserNameChangeView(LoginRequiredMixin, UpdateView):
    form_class = CustomUserChangeForm
    success_url = reverse_lazy('account:settings')
    template_name = 'account/user_name_change.html'

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        messages.success(self.request, 'ユーザー名が変更されました。')
        return super().form_valid(form)


from django.urls import reverse_lazy
from django.contrib.auth.views import PasswordChangeView


class CustomPasswordChangeView(LoginRequiredMixin, PasswordChangeView):
    success_url = reverse_lazy('account:settings')
    template_name = 'account/password_change.html'
    form_class = BootstrapPasswordChangeForm

    def form_valid(self, form):
        messages.success(self.request, 'パスワードが変更されました。')
        return super().form_valid(form)


from django.contrib import messages
from django.urls import reverse_lazy
from django.views.generic import UpdateView
from .forms import CustomUserChangeForm


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
