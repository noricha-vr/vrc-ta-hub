from django.db import migrations, models


def migrate_confirmed_mentions(apps, schema_editor):
    """確認済みの予約を全員への通知に移す。旧列と本文はそのまま残す。"""
    Message = apps.get_model('announcement', 'DiscordScheduledMessage')
    Message.objects.using(schema_editor.connection.alias).filter(
        mention_everyone_confirmed=True,
    ).update(mention='everyone')


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
