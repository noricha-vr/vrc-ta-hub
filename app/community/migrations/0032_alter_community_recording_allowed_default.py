from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('community', '0031_community_default_recording_policy'),
    ]

    operations = [
        migrations.AlterField(
            model_name='community',
            name='recording_allowed',
            field=models.BooleanField(db_default=False, default=False, help_text='オンにすると、この集会の発表がハブの自動撮影の対象になります', verbose_name='撮影を許可する'),
        ),
    ]
