# Generated by Django 4.2.13 on 2024-06-04 06:07

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('community', '0002_community_group_url_community_organizer_url'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='community',
            name='how_to_join',
        ),
    ]