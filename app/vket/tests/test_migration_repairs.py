from importlib import import_module
from types import SimpleNamespace
from unittest.mock import MagicMock

from django.test import SimpleTestCase


migration = import_module("vket.migrations.0007_ensure_stage_registered_at_column")


class EnsureStageRegisteredAtColumnMigrationTests(SimpleTestCase):
    def setUp(self):
        self.schema_editor = MagicMock()
        self.connection = MagicMock()
        self.cursor = MagicMock()
        self.connection.cursor.return_value.__enter__.return_value = self.cursor
        self.schema_editor.connection = self.connection
        self.schema_editor.quote_name.side_effect = lambda name: f"`{name}`"

        self.field = MagicMock()
        self.field.db_type.return_value = "datetime"

        self.participation = MagicMock()
        self.participation._meta.db_table = "vket_participation"
        self.participation._meta.get_field.return_value = self.field

        self.apps = MagicMock()
        self.apps.get_model.return_value = self.participation

    def test_adds_stage_registered_at_when_column_is_missing(self):
        self.connection.introspection.get_table_description.return_value = [
            SimpleNamespace(name="id"),
            SimpleNamespace(name="progress"),
        ]

        migration.add_stage_registered_at_column_if_missing(self.apps, self.schema_editor)

        self.schema_editor.execute.assert_called_once_with(
            "ALTER TABLE `vket_participation` ADD COLUMN `stage_registered_at` datetime NULL"
        )

    def test_skips_when_stage_registered_at_already_exists(self):
        self.connection.introspection.get_table_description.return_value = [
            SimpleNamespace(name="id"),
            SimpleNamespace(name="stage_registered_at"),
        ]

        migration.add_stage_registered_at_column_if_missing(self.apps, self.schema_editor)

        self.schema_editor.execute.assert_not_called()
