# Generated by Django 4.2.17 on 2025-01-08 01:54

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('community', '0002_community_is_accepted'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='community',
            name='is_accepted',
        ),
        migrations.AddField(
            model_name='community',
            name='status',
            field=models.CharField(choices=[('pending', '承認待ち'), ('approved', '承認済み'), ('rejected', '非承認')], db_index=True, default='pending', max_length=20, verbose_name='承認状態'),
        ),
    ]
