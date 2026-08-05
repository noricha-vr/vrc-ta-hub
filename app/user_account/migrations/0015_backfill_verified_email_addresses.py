"""Backfill allauth email addresses for pre-verification users."""

from django.db import migrations


def backfill_verified_email_addresses(apps, schema_editor):
    """Create one verified primary EmailAddress for each existing user.

    Refuse ambiguous ownership rather than attempting a potentially unsafe
    account merge. Values are intentionally omitted from the exception to
    avoid putting email addresses in migration logs.
    """
    User = apps.get_model('user_account', 'CustomUser')
    EmailAddress = apps.get_model('account', 'EmailAddress')

    if User.objects.filter(email='').exists():
        raise RuntimeError('Blank user email during email verification migration')

    for user in User.objects.exclude(email='').iterator():
        email = user.email.lower()
        matches = list(EmailAddress.objects.filter(email__iexact=email))
        if any(address.user_id != user.pk for address in matches):
            raise RuntimeError('EmailAddress ownership conflict during email verification migration')

        own_matches = [address for address in matches if address.user_id == user.pk]
        if len(own_matches) > 1:
            raise RuntimeError('Ambiguous EmailAddress rows during email verification migration')

        EmailAddress.objects.filter(user_id=user.pk, primary=True).update(primary=False)
        if own_matches:
            address = own_matches[0]
            address.email = email
            address.verified = True
            address.primary = True
            address.save(update_fields=['email', 'verified', 'primary'])
        else:
            EmailAddress.objects.create(
                user_id=user.pk,
                email=email,
                verified=True,
                primary=True,
            )


class Migration(migrations.Migration):

    dependencies = [
        ('account', '0009_emailaddress_unique_primary_email'),
        ('user_account', '0014_alter_customuser_user_name'),
    ]

    operations = [
        migrations.RunPython(backfill_verified_email_addresses, migrations.RunPython.noop),
    ]
