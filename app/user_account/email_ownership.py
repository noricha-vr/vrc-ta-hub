"""メールアドレスの持ち主の判定と、持ち主の記録（EmailOwnership）の同期.

持ち主 = CustomUser.email（主アドレス）か、確認済みまたは primary の EmailAddress を持つユーザー。
同じアドレス（小文字にそろえた値）の持ち主は EmailOwnership の一意制約で 1 人に限る。
記録は CustomUser.save() と、ここで登録する EmailAddress のシグナルで合わせるので、
ローカル登録・Discord 登録（フォーム・自動）・副アドレスの確認・管理画面のどの経路にも効く。

同じユーザーの記録の読み書きは、そのユーザーの行をロックしたトランザクションの中で行い、直列にする。
EmailAddress の保存・削除も wrap_email_address_writes で記録と 1 つのトランザクションにまとめる。
"""

import functools

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver

from user_account.models import EmailOwnership

# メール変更で作られた確認待ちの行。確認されるまでは所有の根拠にならないため、
# 他ユーザーの登録・Discord 登録・メール変更を妨げる重複とはみなさない。
# 登録時の未確認 primary 行は CustomUser.email の unique 制約と対になるので対象外。
PENDING_EMAIL_CHANGE = Q(verified=False, primary=False)

# EmailAddress の save / delete を包み済みかの印（ready() が 2 回呼ばれても二重に包まない）
WRAPPED_MARKER = '_email_ownership_wrapped'
# 部分更新（update_fields）でこれを含まない時は、行の user が変わらない
USER_FIELD_NAMES = frozenset({'user', 'user_id'})


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


def lock_owner(user_id: int | None) -> None:
    """user_id の行をロックし、同じユーザーの持ち主の記録の読み書きを直列にする。

    ロックはトランザクションの終わりまで続くので、トランザクションの中で呼ぶ。行が無ければ何もしない。
    """
    users = get_user_model().objects.select_for_update().filter(pk=user_id)
    list(users.values_list('pk', flat=True))


def _is_recorded_owner(key: str, user_id: int) -> bool:
    return EmailOwnership.objects.filter(email=key, user_id=user_id).exists()


def _record_owner(user_id: int, key: str) -> None:
    """lock_owner の後に、user_id を key の持ち主として記録する。"""
    if _is_recorded_owner(key, user_id):
        return
    try:
        with transaction.atomic():
            EmailOwnership.objects.create(email=key, user_id=user_id)
    except IntegrityError:
        if not _is_recorded_owner(key, user_id):
            raise


def _drop_records_except(user_id: int, owned: set[str]) -> None:
    """lock_owner の後に、owned に無い user_id の記録を消す。"""
    EmailOwnership.objects.filter(user_id=user_id).exclude(email__in=owned).delete()


def claim_email(user_id: int, email: str) -> None:
    """user_id を email の持ち主として記録する。

    別ユーザーが持ち主なら一意制約で IntegrityError になる。取り合いは DB の一意制約だけで決め、
    既存の記録を書き換えて奪うことはしない。同じユーザーの同時実行（二重送信など）で先に記録された場合は成功とする。
    """
    key = normalize_email_key(email)
    if not key:
        return
    with transaction.atomic():
        lock_owner(user_id)
        _record_owner(user_id, key)


def release_unowned_emails(user_id: int) -> None:
    """user_id がもう持ち主でないアドレスの記録を消す。

    持ち主かどうかはロックの後に読む。ロックの前に読むと、同じユーザーの保存中のアドレスの記録を消してしまう。
    """
    with transaction.atomic():
        lock_owner(user_id)
        _drop_records_except(user_id, owned_email_keys(user_id))


def sync_email_ownership(user_id: int) -> None:
    """user_id の持ち主の記録を、今の主アドレスと EmailAddress に合わせる。"""
    with transaction.atomic():
        lock_owner(user_id)
        owned = owned_email_keys(user_id)
        # 同時に記録する別トランザクションと行ロックの順番をそろえ、MySQL のデッドロックを避ける
        for key in sorted(owned):
            _record_owner(user_id, key)
        _drop_records_except(user_id, owned)


def _stored_owner_id(address, write_kwargs) -> int | None:
    """書き込みの前の、DB 上の行の user_id。新しい行と、user を書かない部分更新では読まない。"""
    update_fields = write_kwargs.get('update_fields')
    if address.pk is None or (update_fields is not None and not USER_FIELD_NAMES & set(update_fields)):
        return None
    return EmailAddress.objects.filter(pk=address.pk).values_list('user_id', flat=True).first()


def _in_ownership_transaction(write):
    """EmailAddress の書き込みを、持ち主のロックを取ったトランザクションの中で行う。

    ロックは行を書く前に取る。InnoDB は子の行を書く時に親の行へ共有ロックを置くため、
    書いた後に（シグナルの中で）ロックを強めると、同じユーザーの並行した書き込み同士がデッドロックする。
    user を付け替える時は、DB 上の旧ユーザーも id の昇順でロックし、書いた後に旧ユーザーの記録も合わせる
    （post_save / post_delete のシグナルは、インスタンスの今の user しか見ない）。
    """

    @functools.wraps(write)
    def wrapper(self, *args, **kwargs):
        with transaction.atomic():
            stored_owner_id = _stored_owner_id(self, kwargs)
            for user_id in sorted({self.user_id, stored_owner_id} - {None}):
                lock_owner(user_id)
            result = write(self, *args, **kwargs)
            if stored_owner_id not in (None, self.user_id):
                release_unowned_emails(stored_owner_id)
            return result

    setattr(wrapper, WRAPPED_MARKER, True)
    return wrapper


def wrap_email_address_writes() -> None:
    """EmailAddress.save / delete を、持ち主の記録の読み書きと 1 つのトランザクションにまとめる。

    allauth は管理画面のアクションなどで、外側のトランザクションなしに直接 save() を呼ぶ。包まないと、
    保存の前に記録した持ち主（pre_save）が保存の失敗後も残り、そのアドレスがずっと使用中になる。
    """
    for name in ('save', 'delete'):
        write = getattr(EmailAddress, name)
        if not getattr(write, WRAPPED_MARKER, False):
            setattr(EmailAddress, name, _in_ownership_transaction(write))


@receiver(pre_save, sender=EmailAddress, dispatch_uid='email_ownership_claim_address')
def claim_address_before_save(sender, instance, raw=False, **kwargs):
    """確認済みまたは primary の行は、書く前に持ち主を記録する。

    別ユーザーが持ち主なら IntegrityError で行も書かれない。保存は wrap_email_address_writes で
    記録と 1 つのトランザクションになっているので、行の保存が失敗した時は記録も取り消される。
    """
    if raw or not (instance.verified or instance.primary):
        return
    claim_email(instance.user_id, instance.email)


@receiver(pre_delete, sender=EmailAddress, dispatch_uid='email_ownership_lock_before_delete')
def lock_owner_before_delete(sender, instance, **kwargs):
    """行を消す前に持ち主をロックし、保存と同じ順番（ユーザー → 行）にする。

    管理画面の一括削除（QuerySet.delete）やユーザー削除のカスケードはモデルの delete() を通らない。
    Collector は自分のトランザクションの中で、行を消す前に pre_delete を送る。
    """
    lock_owner(instance.user_id)


@receiver([post_save, post_delete], sender=EmailAddress, dispatch_uid='email_ownership_release_addresses')
def release_addresses_after_change(sender, instance, raw=False, **kwargs):
    """確認の取り消し・アドレスの変更・行の削除で持ち主でなくなったアドレスの記録を消す。"""
    if raw:
        return
    release_unowned_emails(instance.user_id)
