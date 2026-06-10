"""Runtime environment normalization tests."""

from __future__ import annotations

import os
from unittest.mock import patch

from django.test import SimpleTestCase

from website.runtime_env import sanitize_sentry_dsn_environment


class SentryDsnEnvironmentTests(SimpleTestCase):
    """SENTRY_DSN normalization before settings import."""

    def test_blank_sentry_dsn_is_removed(self):
        with patch.dict(os.environ, {'SENTRY_DSN': '  \n\t  '}, clear=False):
            sanitize_sentry_dsn_environment()

            self.assertNotIn('SENTRY_DSN', os.environ)

    def test_sentry_dsn_is_stripped(self):
        with patch.dict(
            os.environ,
            {'SENTRY_DSN': '  https://examplePublicKey@sentry.io/1\n'},
            clear=False,
        ):
            sanitize_sentry_dsn_environment()

            self.assertEqual(
                os.environ['SENTRY_DSN'],
                'https://examplePublicKey@sentry.io/1',
            )

    def test_malformed_sentry_dsn_is_removed(self):
        with patch.dict(os.environ, {'SENTRY_DSN': 'examplePublicKey@sentry.io/1'}, clear=False):
            sanitize_sentry_dsn_environment()

            self.assertNotIn('SENTRY_DSN', os.environ)
