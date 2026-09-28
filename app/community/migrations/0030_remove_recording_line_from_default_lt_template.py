"""発表申請テンプレートの旧既定文から【動画撮影】の行を消す。

撮影の可否は発表ごとの選択肢（EventDetail.recording_policy）で聞くようになったため、
設定画面の既定文をそのまま保存していた集会だけを新しい既定文に置き換える。
主催者が手で書き換えたテンプレートは一致しないので触らない。
既定文は後から変わりうるため、定数を import せずここに固定する。
"""

from django.db import migrations

LEGACY_DEFAULT_TEMPLATE = "【発表概要】\n\n【スライド公開】OK / NG\n\n【動画撮影】YouTube公開 / Discord限定 / OK / NG"
NEW_DEFAULT_TEMPLATE = "【発表概要】\n\n【スライド公開】OK / NG"


def replace_legacy_default_template(apps, schema_editor):
    Community = apps.get_model('community', 'Community')
    Community.objects.filter(
        lt_application_template=LEGACY_DEFAULT_TEMPLATE,
    ).update(lt_application_template=NEW_DEFAULT_TEMPLATE)


class Migration(migrations.Migration):

    dependencies = [
        ('community', '0029_community_recording_allowed'),
    ]

    operations = [
        migrations.RunPython(replace_legacy_default_template, migrations.RunPython.noop),
    ]
