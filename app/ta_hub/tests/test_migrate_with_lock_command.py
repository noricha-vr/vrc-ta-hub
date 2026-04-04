from unittest.mock import Mock

from django.core.management.base import CommandError
from django.test import SimpleTestCase

from ta_hub.management.commands.migrate_with_lock import Command


class FakeCursor:
    def __init__(self, responses, executed_sql):
        self.responses = responses
        self.executed_sql = executed_sql

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params):
        self.executed_sql.append((sql, params))

    def fetchone(self):
        return self.responses.pop(0)


class FakeConnection:
    def __init__(self, vendor, responses=None):
        self.vendor = vendor
        self.responses = list(responses or [])
        self.executed_sql = []

    def cursor(self):
        return FakeCursor(self.responses, self.executed_sql)


class MigrateWithLockCommandTests(SimpleTestCase):
    def test_non_mysql_uses_plain_migrate(self):
        connection = FakeConnection(vendor="sqlite")
        command = Command()
        command._get_connection = Mock(return_value=connection)
        command._run_migrate = Mock()

        command.handle(
            database="default",
            noinput=True,
            verbosity=1,
            skip_lock=False,
            lock_name="test-lock",
            lock_timeout=60,
        )

        command._run_migrate.assert_called_once_with(
            database="default",
            interactive=False,
            verbosity=1,
        )
        self.assertEqual(connection.executed_sql, [])

    def test_mysql_acquires_and_releases_lock_around_migrate(self):
        connection = FakeConnection(vendor="mysql", responses=[(1,), (1,)])
        command = Command()
        command._get_connection = Mock(return_value=connection)
        command._run_migrate = Mock()

        command.handle(
            database="default",
            noinput=True,
            verbosity=2,
            skip_lock=False,
            lock_name="test-lock",
            lock_timeout=15,
        )

        command._run_migrate.assert_called_once_with(
            database="default",
            interactive=False,
            verbosity=2,
        )
        self.assertEqual(
            connection.executed_sql,
            [
                ("SELECT GET_LOCK(%s, %s)", ["test-lock", 15]),
                ("SELECT RELEASE_LOCK(%s)", ["test-lock"]),
            ],
        )

    def test_mysql_lock_timeout_raises_error(self):
        connection = FakeConnection(vendor="mysql", responses=[(0,)])
        command = Command()
        command._get_connection = Mock(return_value=connection)
        command._run_migrate = Mock()

        with self.assertRaises(CommandError):
            command.handle(
                database="default",
                noinput=True,
                verbosity=1,
                skip_lock=False,
                lock_name="test-lock",
                lock_timeout=5,
            )

        command._run_migrate.assert_not_called()

