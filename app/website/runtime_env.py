"""Runtime environment normalization for Django entrypoints."""

from __future__ import annotations

import os


def sanitize_sentry_dsn_environment() -> None:
    """Normalize SENTRY_DSN before Django imports settings."""
    raw_dsn = os.environ.get('SENTRY_DSN')
    if raw_dsn is None:
        return

    normalized_dsn = raw_dsn.strip()
    if not normalized_dsn:
        os.environ.pop('SENTRY_DSN', None)
        return

    from sentry_sdk.utils import BadDsn, Dsn

    try:
        Dsn(normalized_dsn)
    except BadDsn:
        os.environ.pop('SENTRY_DSN', None)
        return

    os.environ['SENTRY_DSN'] = normalized_dsn
