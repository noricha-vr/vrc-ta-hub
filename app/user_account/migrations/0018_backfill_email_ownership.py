"""Record the current owner of every email address in EmailOwnership."""

from django.db import migrations
from django.db.models import Q

# Pending email-change rows do not make their user an owner
# (same definition as user_account.email_ownership.PENDING_EMAIL_CHANGE).
PENDING_EMAIL_CHANGE = Q(verified=False, primary=False)
BATCH_SIZE = 500


def _owners_by_email(User, EmailAddress):
    """Map each lower-cased address to the ids of the users that own it."""
    rows = list(User.objects.values_list('pk', 'email'))
    rows += list(EmailAddress.objects.exclude(PENDING_EMAIL_CHANGE).values_list('user_id', 'email'))
    owners = {}
    for user_id, email in rows:
        key = (email or '').strip().lower()
        if key:
            owners.setdefault(key, set()).add(user_id)
    return owners


def backfill_email_ownership(apps, schema_editor):
    """Record one owner per address, or stop when two users own the same address.

    Ambiguous ownership must be resolved by a person (``audit_email_ownership``
    reports the counts), so the migration fails closed instead of picking an
    owner. Addresses are intentionally omitted from the exception to keep them
    out of migration logs.
    """
    User = apps.get_model('user_account', 'CustomUser')
    EmailAddress = apps.get_model('account', 'EmailAddress')
    EmailOwnership = apps.get_model('user_account', 'EmailOwnership')

    owners = _owners_by_email(User, EmailAddress)
    conflicts = sum(1 for user_ids in owners.values() if len(user_ids) > 1)
    if conflicts:
        raise RuntimeError(
            f'Email ownership conflict during email ownership migration: addresses={conflicts}'
        )
    EmailOwnership.objects.bulk_create(
        [EmailOwnership(email=email, user_id=next(iter(user_ids))) for email, user_ids in owners.items()],
        batch_size=BATCH_SIZE,
    )


def remove_email_ownership(apps, schema_editor):
    """Undo the backfill so that applying it again starts from an empty table."""
    apps.get_model('user_account', 'EmailOwnership').objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('account', '0009_emailaddress_unique_primary_email'),
        ('user_account', '0017_emailownership'),
    ]

    operations = [
        migrations.RunPython(backfill_email_ownership, remove_email_ownership),
    ]
