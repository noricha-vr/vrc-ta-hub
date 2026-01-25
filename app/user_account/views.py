from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.contrib.auth.views import PasswordChangeView
from django.core.mail import send_mail
from django.http import JsonResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils.html import strip_tags
from django.utils.safestring import mark_safe
from django.views import View
from django.views.generic import CreateView, TemplateView, ListView
from django.views.generic import UpdateView
from django.conf import settings
from django.utils import timezone

from community.models import Community
from .forms import CustomUserChangeForm
from .forms import CustomUserCreationForm, BootstrapAuthenticationForm, BootstrapPasswordChangeForm
from .models import APIKey


class CustomLoginView(LoginView):
    template_name = 'account/login.html'
    # success_url = reverse_lazy('account:settings')  # ログイン成功後のリダイレクト先
    form_class = BootstrapAuthenticationForm

    def get_success_url(self):
        messages.info(self.request, 'ログインしました。')
        # settings.LOGIN_REDIRECT_URL を使うように変更
        redirect_to = settings.LOGIN_REDIRECT_URL
        return redirect_to


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

        # メーザーへのウェルカムメール
        context = {
            'user': user,
            'login_url': self.request.build_absolute_uri(reverse_lazy('account:login')),
            'discord_url': 'https://discord.gg/6jCkUUb9VN'
        }
        html_message = render_to_string('account/email/welcome.html', context)
        plain_message = strip_tags(html_message)

        # ユーザーへメール送信
        send_mail(
            subject='VRC技術学術系Hub 集会仮登録完了のお知らせ',
            message=plain_message,
            from_email=None,  # settings.pyのDEFAULT_FROM_EMAILが使用されます
            recipient_list=[user.email],
            html_message=html_message,
        )

        # 管理者への通知メール
        admin_context = {
            'user': user,
            'admin_url': self.request.build_absolute_uri(reverse('community:waiting_list')),
        }
        admin_html_message = render_to_string('account/email/admin_notification.html', admin_context)
        admin_plain_message = strip_tags(admin_html_message)

        # 管理者へメール送信
        send_mail(
            subject=f'【VRC技術学術系Hub】集会の仮登録申請: {user.user_name}',
            message=admin_plain_message,
            from_email=None,
            recipient_list=[settings.ADMIN_EMAIL],
            html_message=admin_html_message,
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


class APIKeyListView(LoginRequiredMixin, ListView):
    model = APIKey
    template_name = 'account/api_key_list.html'
    context_object_name = 'api_keys'
    
    def get_queryset(self):
        return self.request.user.api_keys.filter(is_active=True).order_by('-created_at')


class APIKeyCreateView(LoginRequiredMixin, View):
    def post(self, request):
        name = request.POST.get('name', '')
        api_key = APIKey.objects.create(
            user=request.user,
            name=name
        )
        messages.success(request, f'APIキー「{api_key.name or "APIキー"}」を作成しました。キーは一度しか表示されませんので必ず保管してください。')
        messages.warning(request, f'APIキー: {api_key.key}')
        return redirect('account:api_key_list')


class APIKeyDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        try:
            api_key = APIKey.objects.get(pk=pk, user=request.user)
            api_key.is_active = False
            api_key.save()
            messages.success(request, f'APIキー「{api_key.name or "APIキー"}」を無効化しました。')
        except APIKey.DoesNotExist:
            messages.error(request, 'APIキーが見つかりません。')
        return redirect('account:api_key_list')

# for user in CustomUser.objects.all():
#     password = secrets.token_hex(12)  # ランダムな16文字のパスワードを生成
#     user.set_password(password)
#     user.save()
#     print(user.user_name, password)
