# Generated by Django 4.2.13 on 2024-06-08 07:17

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('community', '0002_remove_community_start_from_community_created_at_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='community',
            name='platform',
            field=models.CharField(choices=[('PC', 'PC'), ('All', 'Android・PC'), ('Android', 'Android')], default='All', max_length=10, verbose_name='対応プラットフォーム'),
        ),
    ]