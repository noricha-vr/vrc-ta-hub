"""Tests for the read-only legacy email verification audit."""

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from allauth.account.models import EmailAddress

from tests.factories import make_legacy_user, make_user, make_user_without_email_address

class EmailVerificationBackfillAuditTests(TestCase):
    """Reject legacy states that cannot be migrated without guessing ownership."""

    def test_reports_only_counts_for_a_compatible_database(self):
        make_user(user_name='compatible', email='compatible@example.com')
        stdout = StringIO()

        call_command('audit_email_verification_backfill', stdout=stdout)

        output = stdout.getvalue()
        self.assertIn('users=1 blank=0 ownership_conflicts=0 duplicate_own_rows=0', output)
        self.assertNotIn('compatible@example.com', output)

    def test_rejects_an_email_address_owned_by_another_user(self):
        target = make_user_without_email_address(
            user_name='target',
            email='target@example.com',
            password='testpass123',
        )
        owner = make_user(user_name='owner', email='owner@example.com')
        EmailAddress.objects.create(
            user=owner,
            email=target.email,
            verified=False,
            primary=False,
        )
        stdout = StringIO()

        with self.assertRaises(CommandError):
            call_command('audit_email_verification_backfill', stdout=stdout)

        self.assertIn('ownership_conflicts=1', stdout.getvalue())
        self.assertNotIn(target.email, stdout.getvalue())

    def test_rejects_a_blank_legacy_email(self):
        make_legacy_user(user_name='blank', email='')
        stdout = StringIO()

        with self.assertRaises(CommandError):
            call_command('audit_email_verification_backfill', stdout=stdout)

        self.assertIn('blank=1', stdout.getvalue())
