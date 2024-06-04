# Generated by Django 4.2.13 on 2024-06-04 05:57

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('community', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='community',
            name='group_url',
            field=models.URLField(blank=True, verbose_name='VRChatグループURL'),
        ),
        migrations.AddField(
            model_name='community',
            name='organizer_url',
            field=models.URLField(blank=True, verbose_name='VRChat主催プロフィールURL'),
        ),
    ]