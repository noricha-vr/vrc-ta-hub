from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('twitter', '0012_tweetqueue_generation_token'),
    ]

    operations = [
        migrations.AlterField(
            model_name='twittertemplate',
            name='template',
            field=models.TextField(help_text='利用可能な変数: {event_name}, {date}, {time}, {speaker}, {theme}, {group_url}, {hashtag}', verbose_name='テンプレート'),
        ),
    ]
