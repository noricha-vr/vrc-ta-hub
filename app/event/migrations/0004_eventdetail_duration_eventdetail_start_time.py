# Generated by Django 4.2.13 on 2024-06-19 04:01

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('event', '0003_alter_eventdetail_event'),
    ]

    operations = [
        migrations.AddField(
            model_name='eventdetail',
            name='duration',
            field=models.IntegerField(default=30, help_text='単位は分', verbose_name='発表時間'),
        ),
        migrations.AddField(
            model_name='eventdetail',
            name='start_time',
            field=models.TimeField(default='22:00', verbose_name='開始時刻'),
        ),
    ]
