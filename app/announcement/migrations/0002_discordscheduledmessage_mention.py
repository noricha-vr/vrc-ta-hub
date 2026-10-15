import re

from django.db import migrations, models


def migrate_confirmed_mentions(apps, schema_editor):
    """確認済みの予約を、本文のメンションに合わせて選択に移し、本文からはメンションを外す。

    送信時に選択したメンションを先頭行へ付けるので、本文に残すと二重になり、文字数の上限も超えうる。
    @here だけの予約を @everyone に広げないよう、本文の種類で振り分ける。
    """
    Message = apps.get_model('announcement', 'DiscordScheduledMessage')
    messages = Message.objects.using(schema_editor.connection.alias).filter(mention_everyone_confirmed=True)
    for message in messages:
        message.mention = 'everyone' if '@everyone' in message.body else 'here'
        body = re.sub(r'@(?:everyone|here)[ \t]*', '', message.body)
        message.body = re.sub(r'\A\s*\n', '', body)
        message.save(update_fields=['mention', 'body'])


class Migration(migrations.Migration):
    dependencies = [
        ('announcement', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='discordscheduledmessage',
            name='mention',
            field=models.CharField(
                choices=[('everyone', '@everyone（全員）'), ('here', '@here（オンラインの人）'), ('none', 'なし')],
                db_default='none', default='none', max_length=8, verbose_name='全員への通知',
            ),
        ),
        migrations.RunPython(migrate_confirmed_mentions, migrations.RunPython.noop),
    ]
