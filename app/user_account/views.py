import logging
import requests

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

logger = logging.getLogger(__name__)

from community.models import Community
from .forms import CustomUserChangeForm
from .forms import BootstrapAuthenticationForm, BootstrapPasswordChangeForm
from .models import APIKey


class CustomLoginView(LoginView):
    template_name = 'account/login.html'
    # success_url = reverse_lazy('account:settings')  # ログイン成功後のリダイレクト先
    form_class = BootstrapAuthenticationForm

    def form_valid(self, form):
        """ログイン成功時の処理。rememberフィールドでセッション有効期限を設定。"""
        remember = form.cleaned_data.get('remember')
        response = super().form_valid(form)
        # super().form_valid()の後にセッション設定（login()でセッションが再生成されるため）
        if not remember:
            # チェックが入っていない場合、ブラウザを閉じるとセッションが切れる
            self.request.session.set_expiry(0)
        return response

    def get_success_url(self):
        messages.info(self.request, 'ログインしました。')
        # 親クラスのget_redirect_url()はnextパラメータの安全性を検証する
        # （外部URLへのリダイレクトを防止）
        redirect_url = self.get_redirect_url()
        if redirect_url:
            return redirect_url
        return settings.LOGIN_REDIRECT_URL


class CustomLogoutView(LogoutView):
    next_page = reverse_lazy('account:login')  # ログアウト後のリダイレクト先

    def dispatch(self, request, *args, **kwargs):
        messages.info(request, 'ログアウトしました。')
        return super().dispatch(request, *args, **kwargs)


class RegisterView(TemplateView):
    """新規登録ページ（Discordログインのみ）"""
    template_name = 'account/register.html'


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
        # オーナーとして所属する集会を取得
        from community.models import CommunityMember
        membership = self.request.user.community_memberships.filter(
            role=CommunityMember.Role.OWNER
        ).select_related('community').first()
        context['community'] = membership.community if membership else None
        # 承認されていない場合はメッセージを追加
        if context['community'] and not context['community'].is_accepted:
            message = mark_safe(
                'この集会は現在承認待ちです。既に公開されている技術・学術系集会に承認されると公開されるようになります。'
                'Discord <a href="https://discord.gg/6jCkUUb9VN" target="_blank" rel="noopener noreferrer" class="alert-link">技術・学術系Hub</a>にご参加ください。'
            )
            messages.warning(self.request, message)
        return context


class APIKeyListView(LoginRequiredMixin, ListView):
    model = APIKey
    template_name = 'account/api_key_list.html'
    context_object_name = 'api_keys'
    
    def get_queryset(self):
        return self.request.user.api_keys.filter(is_active=True).order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # 生成直後のAPIキー（平文）はセッションから1回だけ取り出して表示する
        context['new_api_key'] = self.request.session.pop('new_api_key', None)
        context['new_api_key_name'] = self.request.session.pop('new_api_key_name', None)
        return context


class APIKeyCreateView(LoginRequiredMixin, View):
    def post(self, request):
        name = request.POST.get('name', '')
        api_key, raw_key = APIKey.create_with_raw_key(
            user=request.user,
            name=name,
        )
        # messages/cookieに平文キーを載せない（セッションに一時保存→表示後に破棄）
        request.session['new_api_key'] = raw_key
        request.session['new_api_key_name'] = api_key.name or "APIキー"
        messages.success(request, f'APIキー「{api_key.name or "APIキー"}」を作成しました。キーは一度だけ表示されます。必ず安全な場所に保管してください。')
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


class LTApplicationListView(LoginRequiredMixin, ListView):
    """LT申請一覧ページ"""
    template_name = 'account/lt_application_list.html'
    context_object_name = 'applications'

    def get_queryset(self):
        from event.models import EventDetail
        return EventDetail.objects.filter(
            applicant=self.request.user,
            detail_type='LT',
        ).select_related('event', 'event__community').order_by('-event__date', '-created_at')


class LTApplicationEditView(LoginRequiredMixin, UpdateView):
    """LT申請編集ページ"""
    template_name = 'account/lt_application_edit.html'

    def get_form_class(self):
        from event.forms import LTApplicationEditForm
        return LTApplicationEditForm

    def get_queryset(self):
        from event.models import EventDetail
        return EventDetail.objects.filter(
            applicant=self.request.user,
            detail_type='LT',
        ).exclude(status='rejected').select_related('event', 'event__community')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['event'] = self.object.event
        context['community'] = self.object.event.community
        return context

    def form_valid(self, form):
        response = super().form_valid(form)

        generate_blog_flag = form.cleaned_data.get('generate_blog_article', False)
        if (generate_blog_flag and
                (form.instance.slide_file or form.instance.youtube_url)):
            try:
                from event.libs import generate_blog
                from django.conf import settings as django_settings
                blog_output = generate_blog(form.instance, model=django_settings.GEMINI_MODEL)
                if blog_output.title:
                    form.instance.h1 = blog_output.title
                    form.instance.contents = blog_output.text
                    form.instance.meta_description = blog_output.meta_description
                    form.instance.save()
                    messages.success(self.request, "LT申請情報を更新し、記事を自動生成しました。")
                    logger.info(f"記事を自動生成しました: {form.instance.id}")
                else:
                    logger.warning(f"記事の自動生成に失敗しました（空の結果）: {form.instance.id}")
                    messages.warning(self.request, "LT申請情報を更新しましたが、記事の自動生成に失敗しました。")
            except Exception as e:
                logger.error(f"記事の自動生成中にエラーが発生しました: {str(e)}")
                messages.error(self.request, "LT申請情報を更新しましたが、記事の自動生成中にエラーが発生しました。")
        else:
            messages.success(self.request, 'LT申請情報を更新しました。')

        return response

    def get_success_url(self):
        return reverse('account:lt_application_list')


class DiscordRequiredView(LoginRequiredMixin, TemplateView):
    """Discord連携必須ページ."""

    template_name = 'account/discord_required.html'

# for user in CustomUser.objects.all():
#     password = secrets.token_hex(12)  # ランダムな16文字のパスワードを生成
#     user.set_password(password)
#     user.save()
#     print(user.user_name, password)
