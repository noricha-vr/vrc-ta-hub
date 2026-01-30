"""テスト用ユーティリティ関数."""
from django.contrib.auth import get_user_model

from allauth.socialaccount.models import SocialAccount

User = get_user_model()


def create_discord_linked_user(user_name, email, password, discord_uid=None, **extra_fields):
    """Discord連携済みのテストユーザーを作成するヘルパー関数.

    DiscordAuthRequiredMiddleware によってDiscord未連携ユーザーが
    リダイレクトされるため、認証が必要なテストではこの関数を使用する。

    Args:
        user_name: ユーザー名
        email: メールアドレス
        password: パスワード
        discord_uid: Discord UID（デフォルトはuser_nameから生成）
        **extra_fields: Userモデルへの追加フィールド

    Returns:
        作成されたユーザーオブジェクト
    """
    user = User.objects.create_user(
        user_name=user_name,
        email=email,
        password=password,
        **extra_fields,
    )
    SocialAccount.objects.create(
        user=user,
        provider='discord',
        uid=discord_uid or f'discord_{user_name}',
    )
    return user
