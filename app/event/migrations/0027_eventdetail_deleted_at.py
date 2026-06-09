# Generated for EventDetail soft delete (deleted_at column)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('event', '0026_recurrencerule_last_generated_date'),
    ]

    operations = [
        migrations.AddField(
            model_name='eventdetail',
            name='deleted_at',
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                null=True,
                verbose_name='削除日時',
            ),
        ),
    ]
