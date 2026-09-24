"""メールアドレスが他アカウントで使用中かどうかの判定."""

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.db.models import Q

# メール変更で作られた確認待ちの行。確認されるまでは所有の根拠にならないため、
# 他ユーザーの登録・Discord 登録・メール変更を妨げる重複とはみなさない。
# 登録時の未確認 primary 行は CustomUser.email の unique 制約と対になるので対象外。
PENDING_EMAIL_CHANGE = Q(verified=False, primary=False)


def is_email_in_use(email: str, *, exclude_user_id: int | None = None) -> bool:
    """email が exclude_user_id 以外のアカウントで使用中かを返す。

    CustomUser.email と、確認済みまたは primary の EmailAddress を使用中とみなす。
    """
    users = get_user_model().objects.filter(email__iexact=email)
    addresses = EmailAddress.objects.filter(email__iexact=email).exclude(PENDING_EMAIL_CHANGE)
    if exclude_user_id is not None:
        users = users.exclude(pk=exclude_user_id)
        addresses = addresses.exclude(user_id=exclude_user_id)
    return users.exists() or addresses.exists()


def is_email_held_only_by_pending_changes(email: str) -> bool:
    """email を持つ行が他ユーザーの確認待ちの変更行だけかを返す。"""
    has_pending_change = EmailAddress.objects.filter(
        PENDING_EMAIL_CHANGE,
        email__iexact=email,
    ).exists()
    return has_pending_change and not is_email_in_use(email)
