from django.core.management import BaseCommand, CommandError, call_command
from django.db import connections

DEFAULT_LOCK_NAME = "vrc-ta-hub:migrate"
DEFAULT_LOCK_TIMEOUT_SECONDS = 60


class Command(BaseCommand):
    help = "MySQL advisory lock を使って安全に migrate を実行する"

    def add_arguments(self, parser):
        parser.add_argument(
            "--noinput",
            "--no-input",
            action="store_true",
            dest="noinput",
            help="対話入力なしで migrate を実行する",
        )
        parser.add_argument(
            "--database",
            default="default",
            help="migrate を実行するデータベースエイリアス",
        )
        parser.add_argument(
            "--lock-name",
            default=DEFAULT_LOCK_NAME,
            help="MySQL advisory lock の名前",
        )
        parser.add_argument(
            "--lock-timeout",
            type=int,
            default=DEFAULT_LOCK_TIMEOUT_SECONDS,
            help="MySQL advisory lock の取得待ち秒数",
        )
        parser.add_argument(
            "--skip-lock",
            action="store_true",
            help="advisory lock を使わずに migrate を実行する",
        )

    def handle(self, *args, **options):
        database = options["database"]
        connection = self._get_connection(database)

        migrate_kwargs = {
            "database": database,
            "interactive": not options["noinput"],
            "verbosity": options["verbosity"],
        }

        if options["skip_lock"] or connection.vendor != "mysql":
            self._run_migrate(**migrate_kwargs)
            return

        lock_name = options["lock_name"]
        lock_timeout = options["lock_timeout"]

        self.stdout.write(
            f"MySQL advisory lock `{lock_name}` を取得して migration を実行します"
        )

        if not self._acquire_mysql_lock(connection, lock_name, lock_timeout):
            raise CommandError(
                f"MySQL advisory lock `{lock_name}` を {lock_timeout} 秒以内に取得できませんでした"
            )

        try:
            self._run_migrate(**migrate_kwargs)
        finally:
            self._release_mysql_lock(connection, lock_name)

    def _get_connection(self, database):
        return connections[database]

    def _run_migrate(self, **kwargs):
        call_command("migrate", **kwargs)

    def _acquire_mysql_lock(self, connection, lock_name, lock_timeout):
        with connection.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, %s)", [lock_name, lock_timeout])
            result = cursor.fetchone()
        return bool(result and result[0] == 1)

    def _release_mysql_lock(self, connection, lock_name):
        with connection.cursor() as cursor:
            cursor.execute("SELECT RELEASE_LOCK(%s)", [lock_name])
