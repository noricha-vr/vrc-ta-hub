"""Regression tests for the verified EmailAddress backfill migration."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from allauth.account.models import EmailAddress

# The allauth migration that 0015 depends on; include it to get a historical EmailAddress.
ALLAUTH_EMAIL_STATE = ('account', '0009_emailaddress_unique_primary_email')


class EmailAddressBackfillMigrationTests(TransactionTestCase):
    """Exercise the migration from the immediately preceding app state."""

    migrate_from = [('user_account', '0014_alter_customuser_user_name')]
    migrate_to = [('user_account', '0015_backfill_verified_email_addresses')]

    def setUp(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        self.old_apps = self.executor.loader.project_state(self.migrate_from + [ALLAUTH_EMAIL_STATE]).apps
        # Create rows through the historical model: the current model's email
        # ownership signals need the EmailOwnership table, which 0014 lacks.
        self.OldEmailAddress = self.old_apps.get_model('account', 'EmailAddress')

    def tearDown(self):
        # Later tests need the latest schema (e.g. EmailOwnership), not just 0015.
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
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
        stale = self.OldEmailAddress.objects.create(
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
        conflict = self.OldEmailAddress.objects.create(
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
