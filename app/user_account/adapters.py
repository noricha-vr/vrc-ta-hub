"""Discord OAuth用のカスタムアダプター."""
import logging

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth import get_user_model
from django.urls import reverse

logger = logging.getLogger(__name__)

User = get_user_model()


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Discord OAuth認証用のカスタムアダプター.

    既存ユーザーとDiscordアカウントの自動紐付けを行う。
    """

    def is_auto_signup_allowed(self, request, sociallogin):
        """自動サインアップを許可するかどうかを判定.

        メールアドレスが取得できた場合のみ自動サインアップを許可。
        メールが取得できない場合はサインアップフォームを表示して
        ユーザーにメールアドレスを入力してもらう。

        Args:
            request: HTTPリクエスト
            sociallogin: ソーシャルログイン情報

        Returns:
            bool: 自動サインアップを許可するかどうか
        """
        # Discordから取得したデータを確認
        email = sociallogin.account.extra_data.get('email', '')

        if email:
            logger.info("Email found, allowing auto signup")
            return True
        else:
            logger.info("No email found, redirecting to signup form")
            return False

    def pre_social_login(self, request, sociallogin):
        """ソーシャルログイン前の処理.

        process='connect' の場合はログイン中のユーザーへの紐付け処理なので、
        allauthのデフォルト動作に任せる。

        Args:
            request: HTTPリクエスト
            sociallogin: ソーシャルログイン情報
        """
        if sociallogin.is_existing:
            return

        # process='connect' の場合はログイン中のユーザーへの紐付け処理
        # allauthのデフォルト動作に任せる
        if sociallogin.state.get('process') == 'connect':
            logger.info("process='connect': delegating to allauth default behavior")
            return

        discord_id = sociallogin.account.uid
        logger.info(f"Discord login attempt: discord_id={discord_id}")

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

        user.user_name = user_name
        user.email = data.get('email', '')

        logger.info(f"Populating new user: user_name={user.user_name}, discord_id={discord_id}")

        return user

    def save_user(self, request, sociallogin, form=None):
        """ユーザー保存後の処理.

        フォームからuser_nameを取得して設定する。

        Args:
            request: HTTPリクエスト
            sociallogin: ソーシャルログイン情報
            form: フォーム（オプション）

        Returns:
            User: 保存されたユーザーオブジェクト
        """
        user = super().save_user(request, sociallogin, form)

        # フォームからuser_nameを取得して設定
        # allauthのDefaultAccountAdapter.save_userは'username'を見るが、
        # フォームのフィールド名は'user_name'なので明示的に処理する
        if form and 'user_name' in form.cleaned_data:
            user.user_name = form.cleaned_data['user_name']
            user.save(update_fields=['user_name'])
            logger.info(f"Setting user_name from form: {user.user_name}")

        return user

    def get_connect_redirect_url(self, request, socialaccount):
        """ソーシャルアカウント連携後のリダイレクト先を設定.

        デフォルトの/accounts/3rdparty/ではなく、
        設定ページにリダイレクトする。

        Args:
            request: HTTPリクエスト
            socialaccount: 連携されたソーシャルアカウント

        Returns:
            str: リダイレクト先URL
        """
        logger.info(f"Discord account connected for user: {request.user}")
        return reverse('account:settings')
