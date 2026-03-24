"""Discord OAuth用のカスタムアダプター."""
import logging

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialAccount
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
        is_verified = sociallogin.account.extra_data.get('verified') is True

        if email and is_verified:
            logger.info("Verified email found, allowing auto signup")
            return True
        else:
            logger.info("Missing or unverified email, redirecting to signup form")
            return False

    def pre_social_login(self, request, sociallogin):
        """ソーシャルログイン前の処理.

        process='connect' の場合はログイン中のユーザーへの紐付け処理なので、
        allauthのデフォルト動作に任せる。

        Args:
            request: HTTPリクエスト
            sociallogin: ソーシャルログイン情報
        """
        # process='connect' の場合はログイン中のユーザーへの紐付け処理
        # is_existing チェックより先に判定する必要がある
        # （lookup() で is_existing=True になった後でも競合解決が必要なため）
        if sociallogin.state.get('process') == 'connect':
            self._merge_conflicting_account(request, sociallogin)
            return

        if sociallogin.is_existing:
            return

        discord_id = sociallogin.account.uid
        discord_id_suffix = discord_id[-4:] if len(discord_id) > 4 else discord_id
        email = sociallogin.account.extra_data.get('email', '')
        is_verified = sociallogin.account.extra_data.get('verified') is True
        logger.info("Discord login attempt: discord_id_suffix=%s", discord_id_suffix)

        if not email or not is_verified:
            if email and not is_verified:
                logger.warning(
                    "Skipping auto connect for unverified Discord email: discord_id_suffix=%s",
                    discord_id_suffix,
                )
            return

        existing_user = User.objects.filter(email__iexact=email).first()
        if not existing_user:
            return

        if SocialAccount.objects.filter(
            user=existing_user,
            provider=sociallogin.account.provider,
        ).exclude(uid=discord_id).exists():
            logger.warning(
                "Skipping auto connect because user already has another Discord account: user_id=%s",
                existing_user.id,
            )
            return

        if SocialAccount.objects.filter(
            provider=sociallogin.account.provider,
            uid=discord_id,
        ).exclude(user=existing_user).exists():
            logger.warning(
                "Discord account conflict detected for existing user: user_id=%s",
                existing_user.id,
            )
            return

        logger.info(
            "Connecting Discord account to existing user: user_id=%s, discord_id_suffix=%s",
            existing_user.id,
            discord_id_suffix,
        )
        sociallogin.connect(request, existing_user)

    def _merge_conflicting_account(self, request, sociallogin):
        """process='connect' 時に競合するSocialAccountがあればマージする.

        ログイン中のユーザーがDiscord接続を試みた際、同じDiscord UIDが
        別アカウントに紐付いている場合、CommunityMemberを移動して
        競合するSocialAccountを削除する。
        """
        from community.models import CommunityMember

        discord_uid = sociallogin.account.uid
        current_user = request.user

        conflicting_sa = SocialAccount.objects.filter(
            provider='discord', uid=discord_uid,
        ).exclude(user=current_user).first()

        if not conflicting_sa:
            return

        other_user = conflicting_sa.user
        discord_id_suffix = discord_uid[-4:] if len(discord_uid) > 4 else discord_uid

        # CommunityMember を移動（current_user にないものだけ）
        for cm in CommunityMember.objects.filter(user=other_user):
            if not CommunityMember.objects.filter(
                user=current_user, community=cm.community,
            ).exists():
                cm.user = current_user
                cm.save()
                logger.info(
                    "Auto-merge: moved CommunityMember community_id=%s from user_id=%s to user_id=%s",
                    cm.community_id, other_user.id, current_user.id,
                )

        # 競合するSocialAccountを現在のユーザーに再割り当て
        conflicting_sa.user = current_user
        conflicting_sa.extra_data = sociallogin.account.extra_data
        conflicting_sa.save()

        # socialloginの状態を更新（allauthのdo_connectが正しく処理できるように）
        # is_existing は user.pk の存在で自動判定されるため、user の設定だけでOK
        sociallogin.account = conflicting_sa
        sociallogin.user = current_user

        logger.info(
            "Auto-merge: reassigned SocialAccount discord_id_suffix=%s "
            "from user_id=%s to user_id=%s",
            discord_id_suffix, other_user.id, current_user.id,
        )

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
