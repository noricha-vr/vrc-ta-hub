import hashlib

from django.contrib import messages
from django.shortcuts import render

# Create your views here.
import secrets
from django.contrib.auth.hashers import make_password

from account.models import CustomUser
from community.models import Community
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import reverse_lazy

from django.views.generic import CreateView, TemplateView
from django.urls import reverse_lazy
from .forms import CustomUserCreationForm


class CustomLoginView(LoginView):
    template_name = 'account/login.html'
    success_url = reverse_lazy('ta_hub:index')  # ログイン成功後のリダイレクト先

    def get_success_url(self):
        messages.success(self.request, 'ログインしました。')
        return self.success_url


class CustomLogoutView(LogoutView):
    next_page = reverse_lazy('account:login')  # ログアウト後のリダイレクト先


class CustomUserCreateView(CreateView):
    form_class = CustomUserCreationForm
    template_name = 'account/register.html'
    success_url = reverse_lazy('account:login')


from django.contrib.auth.views import PasswordChangeView
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
        messages.success(self.request, 'ユーザー名が正常に変更されました。')
        return super().form_valid(form)


from django.contrib import messages
from django.urls import reverse_lazy
from django.contrib.auth.views import PasswordChangeView


class CustomPasswordChangeView(LoginRequiredMixin, PasswordChangeView):
    success_url = reverse_lazy('account:settings')
    template_name = 'account/password_change.html'

    def form_valid(self, form):
        messages.success(self.request, 'パスワードが正常に変更されました。')
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
        messages.success(self.request, 'ユーザー情報が正常に更新されました。')
        return super().form_valid(form)


class SettingsView(LoginRequiredMixin, TemplateView):
    template_name = 'account/settings.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['community'] = Community.objects.filter(custom_user=self.request.user).first()
        return context

# for user in CustomUser.objects.all():
#     password = secrets.token_hex(12)  # ランダムな16文字のパスワードを生成
#     user.set_password(password)
#     user.save()
#     print(user.user_name, password)
