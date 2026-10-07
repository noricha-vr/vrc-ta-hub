"""Audit email ownership before and after the EmailOwnership migrations."""

from collections import defaultdict
from itertools import chain

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, connection, transaction

from allauth.account.models import EmailAddress

from user_account.email_ownership import (
    PENDING_EMAIL_CHANGE,
    normalize_email_key,
    release_unowned_emails,
    sync_email_ownership,
)
from user_account.models import EmailOwnership


def collect_email_owners() -> dict[str, set[int]]:
    """小文字にそろえたアドレスごとに、持ち主（主アドレスか確認済み・primary の EmailAddress）の ID を返す。"""
    owners = defaultdict(set)
    users = get_user_model().objects.values_list('pk', 'email')
    addresses = EmailAddress.objects.exclude(PENDING_EMAIL_CHANGE).values_list('user_id', 'email')
    for user_id, email in chain(users.iterator(), addresses.iterator()):
        key = normalize_email_key(email)
        if key:
            owners[key].add(user_id)
    return owners


class Command(BaseCommand):
    """Report anonymized counts; change data only with --repair."""

    help = (
        'メールアドレスを 2 人以上が持っている件数（conflicts）と、持ち主の記録 EmailOwnership の'
        'ずれ（missing / stale）を件数だけ表示します。アドレスは表示しません。'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--repair',
            action='store_true',
            help='持ち主の記録を今のアカウントデータに合わせ直します（書き込みあり。conflicts は直しません）。',
        )

    def handle(self, *args, **options):
        has_records = EmailOwnership._meta.db_table in connection.introspection.table_names()
        if options['repair']:
            if not has_records:
                raise CommandError('EmailOwnership table is missing; apply user_account migrations first.')
            self._repair()
        counts = self._count(has_records)
        self.stdout.write(' '.join(f'{name}={value}' for name, value in counts.items()))
        if any(value for name, value in counts.items() if name != 'addresses'):
            raise CommandError('Email ownership audit failed; no account data was changed by the audit.')
        self.stdout.write(self.style.SUCCESS('Email ownership audit passed.'))

    @staticmethod
    def _count(has_records: bool) -> dict[str, int]:
        owners = collect_email_owners()
        counts = {
            'addresses': len(owners),
            'conflicts': sum(1 for user_ids in owners.values() if len(user_ids) > 1),
        }
        if has_records:
            expected = {(email, user_id) for email, user_ids in owners.items() for user_id in user_ids}
            recorded = set(EmailOwnership.objects.values_list('email', 'user_id'))
            counts['missing'] = len(expected - recorded)
            counts['stale'] = len(recorded - expected)
        return counts

    def _repair(self) -> None:
        """古い記録を全員分消してから記録し直す（消す前に記録すると取り合いで 1 回で収束しないため）。"""
        user_ids = list(get_user_model().objects.values_list('pk', flat=True))
        for user_id in user_ids:
            release_unowned_emails(user_id)
        conflicted = 0
        for user_id in user_ids:
            try:
                with transaction.atomic():
                    sync_email_ownership(user_id)
            except IntegrityError:
                conflicted += 1
        self.stdout.write(f'repair_conflicted_users={conflicted}')
