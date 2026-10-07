"""メールアドレスの持ち主の判定と、持ち主の記録（EmailOwnership）の同期.

持ち主 = CustomUser.email（主アドレス）か、確認済みまたは primary の EmailAddress を持つユーザー。
同じアドレス（小文字にそろえた値）の持ち主は EmailOwnership の一意制約で 1 人に限る。
記録は CustomUser.save() と、ここで登録する EmailAddress のシグナルで合わせるので、
ローカル登録・Discord 登録（フォーム・自動）・副アドレスの確認・管理画面のどの経路にも効く。
"""

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from user_account.models import EmailOwnership

# メール変更で作られた確認待ちの行。確認されるまでは所有の根拠にならないため、
# 他ユーザーの登録・Discord 登録・メール変更を妨げる重複とはみなさない。
# 登録時の未確認 primary 行は CustomUser.email の unique 制約と対になるので対象外。
PENDING_EMAIL_CHANGE = Q(verified=False, primary=False)


def normalize_email_key(email: str | None) -> str:
    """持ち主の判定に使う形（前後の空白を除いた小文字）にする。"""
    return (email or '').strip().lower()


def is_email_in_use(email: str, *, exclude_user_id: int | None = None) -> bool:
    """email が exclude_user_id 以外のアカウントで使用中かを返す。

    CustomUser.email、確認済みまたは primary の EmailAddress、持ち主の記録のどれかにあれば使用中とみなす。
    """
    users = get_user_model().objects.filter(email__iexact=email)
    addresses = EmailAddress.objects.filter(email__iexact=email).exclude(PENDING_EMAIL_CHANGE)
    owners = EmailOwnership.objects.filter(email=normalize_email_key(email))
    if exclude_user_id is not None:
        users = users.exclude(pk=exclude_user_id)
        addresses = addresses.exclude(user_id=exclude_user_id)
        owners = owners.exclude(user_id=exclude_user_id)
    return users.exists() or addresses.exists() or owners.exists()


def is_email_held_only_by_pending_changes(email: str) -> bool:
    """email を持つ行が他ユーザーの確認待ちの変更行だけかを返す。"""
    has_pending_change = EmailAddress.objects.filter(
        PENDING_EMAIL_CHANGE,
        email__iexact=email,
    ).exists()
    return has_pending_change and not is_email_in_use(email)


def owned_email_keys(user_id: int) -> set[str]:
    """user_id が持ち主のアドレス（主アドレスと、確認済みまたは primary の EmailAddress）を返す。"""
    emails = set(
        EmailAddress.objects.filter(user_id=user_id)
        .exclude(PENDING_EMAIL_CHANGE)
        .values_list('email', flat=True)
    )
    emails.update(get_user_model().objects.filter(pk=user_id).values_list('email', flat=True))
    return {normalize_email_key(email) for email in emails} - {''}


def _is_recorded_owner(key: str, user_id: int) -> bool:
    return EmailOwnership.objects.filter(email=key, user_id=user_id).exists()


def claim_email(user_id: int, email: str) -> None:
    """user_id を email の持ち主として記録する。

    別ユーザーが持ち主なら一意制約で IntegrityError になる。取り合いは DB の一意制約だけで決め、
    既存の記録を書き換えて奪うことはしない。同じユーザーの同時実行（二重送信など）で先に記録された場合は成功とする。
    """
    key = normalize_email_key(email)
    if not key or _is_recorded_owner(key, user_id):
        return
    try:
        with transaction.atomic():
            EmailOwnership.objects.create(email=key, user_id=user_id)
    except IntegrityError:
        if not _is_recorded_owner(key, user_id):
            raise


def release_unowned_emails(user_id: int, owned: set[str] | None = None) -> None:
    """user_id がもう持ち主でないアドレスの記録を消す。"""
    if owned is None:
        owned = owned_email_keys(user_id)
    EmailOwnership.objects.filter(user_id=user_id).exclude(email__in=owned).delete()


def sync_email_ownership(user_id: int) -> None:
    """user_id の持ち主の記録を、今の主アドレスと EmailAddress に合わせる。"""
    owned = owned_email_keys(user_id)
    # 同時に記録する別トランザクションと行ロックの順番をそろえ、MySQL のデッドロックを避ける
    for key in sorted(owned):
        claim_email(user_id, key)
    release_unowned_emails(user_id, owned)


@receiver(pre_save, sender=EmailAddress, dispatch_uid='email_ownership_claim_address')
def claim_address_before_save(sender, instance, raw=False, **kwargs):
    """確認済みまたは primary の行は、書く前に持ち主を記録する。

    別ユーザーが持ち主なら IntegrityError で行も書かれないため、呼び出し元がトランザクションの外でも
    記録の無い持ち主は生まれない。
    """
    if raw or not (instance.verified or instance.primary):
        return
    claim_email(instance.user_id, instance.email)


@receiver([post_save, post_delete], sender=EmailAddress, dispatch_uid='email_ownership_release_addresses')
def release_addresses_after_change(sender, instance, raw=False, **kwargs):
    """確認の取り消し・アドレスの変更・行の削除で持ち主でなくなったアドレスの記録を消す。"""
    if raw:
        return
    release_unowned_emails(instance.user_id)
