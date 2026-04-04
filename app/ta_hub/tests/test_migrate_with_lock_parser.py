from django.test import SimpleTestCase

from ta_hub.management.commands.migrate_with_lock import Command


class MigrateWithLockParserTests(SimpleTestCase):
    def test_parser_accepts_noinput_flag(self):
        parser = Command().create_parser("manage.py", "migrate_with_lock")

        options = parser.parse_args(["--noinput"])

        self.assertTrue(options.noinput)
