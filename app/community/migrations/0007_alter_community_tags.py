# Generated by Django 4.2.13 on 2024-06-13 15:37

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('community', '0006_remove_community_weekday_community_weekdays'),
    ]

    operations = [
        migrations.AlterField(
            model_name='community',
            name='tags',
            field=models.JSONField(default=list, max_length=10, verbose_name='タグ'),
        ),
    ]
