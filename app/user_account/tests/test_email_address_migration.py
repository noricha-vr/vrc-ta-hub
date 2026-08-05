"""Regression tests for the verified EmailAddress backfill migration."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from allauth.account.models import EmailAddress


class EmailAddressBackfillMigrationTests(TransactionTestCase):
    """Exercise the migration from the immediately preceding app state."""

    migrate_from = [('user_account', '0014_alter_customuser_user_name')]
    migrate_to = [('user_account', '0015_backfill_verified_email_addresses')]

    def setUp(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        self.old_apps = self.executor.loader.project_state(self.migrate_from).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate(self.migrate_to)
        super().tearDown()

    def _migrate_forward(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to)
        return self.executor.loader.project_state(self.migrate_to).apps

    def test_creates_verified_primary_address_and_repairs_primary_state(self):
        """Create the matching verified primary address and normalize existing rows."""
        User = self.old_apps.get_model('user_account', 'CustomUser')
        user = User.objects.create(user_name='legacy', email='Legacy@Example.com')
        new_user = User.objects.create(user_name='new', email='new@example.com')
        stale = EmailAddress.objects.create(
            user_id=user.pk,
            email='legacy@example.com',
            verified=False,
            primary=False,
        )

        self._migrate_forward()
        address = EmailAddress.objects.get(pk=stale.pk)

        self.assertEqual(address.email, 'legacy@example.com')
        self.assertTrue(address.verified)
        self.assertTrue(address.primary)
        self.assertTrue(EmailAddress.objects.filter(
            user_id=new_user.pk,
            email='new@example.com',
            verified=True,
            primary=True,
        ).exists())

    def test_stops_on_email_address_owned_by_another_user(self):
        """Fail closed instead of assigning an ambiguous address to a user."""
        User = self.old_apps.get_model('user_account', 'CustomUser')
        target = User.objects.create(user_name='target', email='target@example.com')
        owner = User.objects.create(user_name='owner', email='owner@example.com')
        conflict = EmailAddress.objects.create(
            user_id=owner.pk,
            email=target.email,
            verified=False,
            primary=True,
        )

        with self.assertRaisesRegex(RuntimeError, 'ownership conflict'):
            self._migrate_forward()
        conflict.delete()

    def test_stops_when_a_legacy_user_has_no_email(self):
        """Fail closed because a blank address cannot be made login-compatible."""
        User = self.old_apps.get_model('user_account', 'CustomUser')
        blank_user = User.objects.create(user_name='blank', email='')

        with self.assertRaisesRegex(RuntimeError, 'Blank user email'):
            self._migrate_forward()
        blank_user.delete()
