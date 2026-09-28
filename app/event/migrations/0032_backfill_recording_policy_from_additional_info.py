"""既存の発表申請の自由記述（additional_info）にある【動画撮影】の回答から撮影の扱いを決める。

発表申請テンプレートの旧既定文には「【動画撮影】YouTube公開 / Discord限定 / OK / NG」の行があり、
発表者は不要な選択肢を消して回答していた。その回答を recording_policy に移す。

判定の規則は event/recording_policy_answers.py（management command
``backfill_recording_policy`` と共有）。【動画撮影】の行が無い発表は public のまま。
"""

from django.db import migrations

# 判定は management command と共有する純粋関数（モデルを import しない）
from event.recording_policy_answers import RECORDING_KEYWORD, plan_policy_changes

UPDATE_CHUNK_SIZE = 500


def backfill_recording_policy(apps, schema_editor):
    EventDetail = apps.get_model('event', 'EventDetail')
    candidates = EventDetail._base_manager.filter(
        additional_info__contains=RECORDING_KEYWORD,
    ).values_list('pk', 'additional_info')

    for policy, pks in plan_policy_changes(candidates.iterator()).items():
        for start in range(0, len(pks), UPDATE_CHUNK_SIZE):
            EventDetail._base_manager.filter(
                pk__in=pks[start:start + UPDATE_CHUNK_SIZE],
            ).update(recording_policy=policy)


class Migration(migrations.Migration):

    dependencies = [
        ('event', '0031_eventdetail_recording_policy'),
    ]

    operations = [
        migrations.RunPython(backfill_recording_policy, migrations.RunPython.noop),
    ]
