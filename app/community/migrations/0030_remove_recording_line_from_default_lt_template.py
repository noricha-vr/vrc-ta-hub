"""発表申請テンプレートの旧既定文から【動画撮影】の行を消す。

撮影の可否は発表ごとの選択肢（EventDetail.recording_policy）で聞くようになったため、
設定画面の既定文をそのまま保存していた集会だけを新しい既定文に置き換える。
主催者が手で書き換えたテンプレートは一致しないので触らない。
既定文は後から変わりうるため、定数を import せずここに固定する。

一致判定は DB の照合順序（大文字小文字・末尾空白を同一視しうる）に頼らず、
候補を取り出してから Python の == で比べる。
逆方向（ロールバック）では、移行後に主催者が同じ新既定文で保存した集会も旧既定文に戻る。
"""

from django.db import migrations

LEGACY_DEFAULT_TEMPLATE = "【発表概要】\n\n【スライド公開】OK / NG\n\n【動画撮影】YouTube公開 / Discord限定 / OK / NG"
NEW_DEFAULT_TEMPLATE = "【発表概要】\n\n【スライド公開】OK / NG"
TEMPLATE_PREFIX = "【発表概要】"


def _replace_exact(apps, current, replacement):
    Community = apps.get_model('community', 'Community')
    candidates = Community._base_manager.filter(
        lt_application_template__startswith=TEMPLATE_PREFIX,
    ).values_list('pk', 'lt_application_template')
    pks = [pk for pk, template in candidates if template == current]
    if pks:
        Community._base_manager.filter(pk__in=pks).update(lt_application_template=replacement)


def replace_legacy_default_template(apps, schema_editor):
    _replace_exact(apps, LEGACY_DEFAULT_TEMPLATE, NEW_DEFAULT_TEMPLATE)


def restore_legacy_default_template(apps, schema_editor):
    _replace_exact(apps, NEW_DEFAULT_TEMPLATE, LEGACY_DEFAULT_TEMPLATE)


class Migration(migrations.Migration):

    dependencies = [
        ('community', '0029_community_recording_allowed'),
    ]

    operations = [
        migrations.RunPython(replace_legacy_default_template, restore_legacy_default_template),
    ]
