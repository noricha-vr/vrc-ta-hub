"""Discord認証を強制するミドルウェア."""
from django.conf import settings
from django.shortcuts import redirect
from allauth.socialaccount.models import SocialAccount


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
