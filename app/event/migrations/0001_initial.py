# Generated by Django 4.2.13 on 2024-06-04 05:47

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('community', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Event',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField(db_index=True, verbose_name='開催日')),
                ('start_time', models.TimeField(default='22:00', verbose_name='開始時刻')),
                ('duration', models.IntegerField(default=60, help_text='単位は分', verbose_name='開催時間')),
                ('weekday', models.CharField(blank=True, choices=[('Sun', '日曜日'), ('Mon', '月曜日'), ('Tue', '火曜日'), ('Wed', '水曜日'), ('Thu', '木曜日'), ('Fri', '金曜日'), ('Sat', '土曜日'), ('Other', 'その他')], max_length=5, verbose_name='曜日')),
                ('youtube_url', models.URLField(blank=True, verbose_name='YouTube URL')),
                ('materials_url', models.URLField(blank=True, verbose_name='資料 URL')),
                ('speakers', models.CharField(max_length=200, verbose_name='登壇者')),
                ('theme', models.CharField(max_length=100, verbose_name='テーマ')),
                ('overview', models.TextField(verbose_name='概要')),
                ('meeting', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='community.community', verbose_name='集会')),
            ],
            options={
                'verbose_name': 'イベント',
                'verbose_name_plural': 'イベント',
                'db_table': 'event',
            },
        ),
    ]
