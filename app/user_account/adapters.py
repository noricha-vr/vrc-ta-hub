"""Discord OAuth用のカスタムアダプター."""
import logging

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)


def on_authentication_error(sender, request, provider_id, error, exception, extra_context, **kwargs):
    """認証エラー時のログ出力."""
    logger.error(f"OAuth authentication error: provider={provider_id}, error={error}, exception={exception}")
    if extra_context:
        logger.error(f"Extra context: {extra_context}")


# シグナルを接続
try:
    from allauth.socialaccount.signals import authentication_error
    authentication_error.connect(on_authentication_error)
    logger.info("Connected authentication_error signal handler")
except ImportError:
    logger.warning("Could not import authentication_error signal")

User = get_user_model()


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Discord OAuth認証用のカスタムアダプター.

    既存ユーザーとDiscordアカウントの自動紐付けを行う。
    """

    def is_auto_signup_allowed(self, request, sociallogin):
        """自動サインアップを許可するかどうかを判定.

        Discordの場合はメールがなくてもプレースホルダーメールを使用するため、
        常に自動サインアップを許可する。

        Args:
            request: HTTPリクエスト
            sociallogin: ソーシャルログイン情報

        Returns:
            bool: 自動サインアップを許可するかどうか
        """
        # Discordから取得したデータを確認
        email = sociallogin.account.extra_data.get('email', '')

        if email:
            logger.info(f"Email found, allowing auto signup: {email}")
        else:
            logger.info("No email found, using placeholder email for auto signup")

        # Discordの場合はメールがなくてもプレースホルダーを使うので常に許可
        return True

    def pre_social_login(self, request, sociallogin):
        """ソーシャルログイン前の処理.

        discord_idで既存ユーザーを検索し、見つかった場合は自動的に紐付ける。

        Args:
            request: HTTPリクエスト
            sociallogin: ソーシャルログイン情報
        """
        if sociallogin.is_existing:
            return

        discord_id = sociallogin.account.uid
        logger.info(f"Discord login attempt: discord_id={discord_id}")

        try:
            existing_user = User.objects.get(discord_id=discord_id)
            sociallogin.connect(request, existing_user)
            logger.info(f"Connected Discord account to existing user: {existing_user.user_name}")
        except User.DoesNotExist:
            logger.info(f"No existing user found with discord_id={discord_id}")

    def populate_user(self, request, sociallogin, data):
        """新規ユーザー作成時のフィールド設定.

        Discordから取得した情報でユーザーを作成する。
        ユーザー名が既存ユーザーと衝突する場合はユニーク化する。

        Args:
            request: HTTPリクエスト
            sociallogin: ソーシャルログイン情報
            data: Discordから取得したユーザーデータ

        Returns:
            User: 設定されたユーザーオブジェクト
        """
        user = super().populate_user(request, sociallogin, data)

        discord_id = sociallogin.account.uid
        discord_username = data.get('username', '')

        # ベースとなるユーザー名を決定
        base_name = discord_username or f"discord_{discord_id}"
        user_name = base_name

        # ユーザー名の衝突を確認し、衝突時はユニーク化
        counter = 1
        while User.objects.filter(user_name=user_name).exists():
            if counter == 1:
                # 最初の衝突時はdiscord_idの先頭8文字を付加
                discord_id_suffix = discord_id[:8]
                user_name = f"{base_name}_{discord_id_suffix}"
            else:
                # それ以降はカウンターを付加
                user_name = f"{base_name}_{counter}"
            counter += 1

        user.discord_id = discord_id
        user.user_name = user_name

        # メールが取得できない場合はプレースホルダーメールを使用
        email = data.get('email', '')
        if not email:
            email = f"discord_{discord_id}@placeholder.vrc-ta-hub.com"
            logger.info(f"Email not provided, using placeholder: {email}")
        user.email = email

        logger.info(f"Populating new user: user_name={user.user_name}, discord_id={discord_id}")

        return user

    def save_user(self, request, sociallogin, form=None):
        """ユーザー保存後の処理.

        discord_idが確実に設定されていることを確認する。

        Args:
            request: HTTPリクエスト
            sociallogin: ソーシャルログイン情報
            form: フォーム（オプション）

        Returns:
            User: 保存されたユーザーオブジェクト
        """
        user = super().save_user(request, sociallogin, form)

        discord_id = sociallogin.account.uid
        if not user.discord_id:
            user.discord_id = discord_id
            user.save()
            logger.info(f"Saved discord_id={discord_id} for user: {user.user_name}")

        return user
