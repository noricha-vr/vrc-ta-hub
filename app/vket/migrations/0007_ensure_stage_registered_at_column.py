from django.db import migrations, models


def ensure_stage_registered_at_column(apps, schema_editor):
    VketParticipation = apps.get_model("vket", "VketParticipation")
    table_name = VketParticipation._meta.db_table

    with schema_editor.connection.cursor() as cursor:
        existing_columns = {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(
                cursor,
                table_name,
            )
        }

    if "stage_registered_at" in existing_columns:
        return

    field = models.DateTimeField("ステージ登録日時", null=True, blank=True)
    field.set_attributes_from_name("stage_registered_at")
    schema_editor.add_field(VketParticipation, field)


class Migration(migrations.Migration):
    dependencies = [
        ("vket", "0006_alter_vketparticipation_progress"),
    ]

    operations = [
        migrations.RunPython(
            ensure_stage_registered_at_column,
            migrations.RunPython.noop,
        ),
    ]
