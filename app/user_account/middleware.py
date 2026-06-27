"""認証補助ミドルウェア."""
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.shortcuts import redirect

from allauth.socialaccount.models import SocialAccount

logger = logging.getLogger(__name__)


class DebugLoginSkipMiddleware:
    """DEBUG時のみ未ログインリクエストへデバッグユーザーを割り当てる."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._should_apply(request):
            request.user = self._get_or_create_debug_user()
        return self.get_response(request)

    def _should_apply(self, request):
        """デバッグログインスキップを適用すべきか判定する."""
        if not getattr(settings, 'DEBUG', False):
            return False
        if not getattr(settings, 'DEBUG_LOGIN_SKIP', False):
            return False
        return not request.user.is_authenticated

    def _get_or_create_debug_user(self):
        """デバッグ用 staff ユーザーを取得または作成する."""
        User = get_user_model()
        user_name = settings.DEBUG_LOGIN_SKIP_USER_NAME
        email = settings.DEBUG_LOGIN_SKIP_USER_EMAIL

        try:
            user = User.objects.get(user_name=user_name)
            update_fields = []
            if not user.is_active:
                user.is_active = True
                update_fields.append('is_active')
            if not user.is_staff:
                user.is_staff = True
                update_fields.append('is_staff')
            if update_fields:
                user.save(update_fields=update_fields)
            return user
        except User.DoesNotExist:
            user = User(
                user_name=user_name,
                email=email,
                display_name=user_name,
                is_active=True,
                is_staff=True,
                is_superuser=False,
            )
            user.set_unusable_password()
            user.save()
            logger.debug('Debug login skip user created: %s', user_name)
            return user


class DiscordAuthRequiredMiddleware:
    """ログイン済みでDiscord未連携のユーザーをDiscord連携ページへリダイレクト."""

    # リダイレクトを除外するパス（無限ループ防止）
    EXEMPT_PATHS = [
        '/account/logout/',
        '/accounts/discord/',         # Discord OAuth フロー
        '/accounts/social/signup/',   # ソーシャルサインアップ
        '/account/discord-required/', # 新しい案内ページ
        '/static/',
        '/media/',
        '/admin/',                    # 管理画面（ログイン用）
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._should_redirect_to_discord(request):
            return redirect('account:discord_required')
        return self.get_response(request)

    def _should_redirect_to_discord(self, request):
        """Discord連携ページへリダイレクトすべきか判定.

        Args:
            request: HTTPリクエストオブジェクト

        Returns:
            bool: リダイレクトすべき場合はTrue
        """
        # 設定でミドルウェアを無効化している場合はスキップ
        # テスト時に DISCORD_AUTH_REQUIRED = False で無効化可能
        if not getattr(settings, 'DISCORD_AUTH_REQUIRED', True):
            return False

        # 未認証ユーザーはスキップ
        if not request.user.is_authenticated:
            return False

        # 除外パスはスキップ
        path = request.path
        if any(path.startswith(exempt) for exempt in self.EXEMPT_PATHS):
            return False

        # Discord連携済みならスキップ
        if SocialAccount.objects.filter(user=request.user, provider='discord').exists():
            return False

        # Discord未連携 → リダイレクト
        return True
