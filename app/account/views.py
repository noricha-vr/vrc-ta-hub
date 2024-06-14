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

from django.views.generic import CreateView
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
