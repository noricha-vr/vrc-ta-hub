"""Audit legacy email ownership before the verified-address backfill."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from allauth.account.models import EmailAddress


class Command(BaseCommand):
    """Report anonymized compatibility counts without changing account data."""

    help = '0015 email verification backfill の競合を読み取り専用で監査します。'

    def handle(self, *args, **options):
        User = get_user_model()
        total = User.objects.count()
        blank = 0
        ownership_conflicts = 0
        duplicate_own_rows = 0

        for user in User.objects.only('pk', 'email').iterator():
            if not user.email:
                blank += 1
                continue
            matches = list(
                EmailAddress.objects.filter(email__iexact=user.email).only('user_id')
            )
            if any(address.user_id != user.pk for address in matches):
                ownership_conflicts += 1
            if sum(address.user_id == user.pk for address in matches) > 1:
                duplicate_own_rows += 1

        self.stdout.write(
            'users={total} blank={blank} ownership_conflicts={ownership} '
            'duplicate_own_rows={duplicates}'.format(
                total=total,
                blank=blank,
                ownership=ownership_conflicts,
                duplicates=duplicate_own_rows,
            )
        )
        if blank or ownership_conflicts or duplicate_own_rows:
            raise CommandError(
                'Email verification backfill audit failed; no account data was changed.'
            )
        self.stdout.write(self.style.SUCCESS('Email verification backfill audit passed.'))
