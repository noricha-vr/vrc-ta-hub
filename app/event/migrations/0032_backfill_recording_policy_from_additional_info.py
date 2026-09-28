"""既存の発表申請の自由記述（additional_info）にある【動画撮影】の回答から撮影の扱いを決める。

発表申請テンプレートの旧既定文には「【動画撮影】YouTube公開 / Discord限定 / OK / NG」の行があり、
発表者は不要な選択肢を消して回答していた。その回答を recording_policy に移す。

- 選択肢が 1 つだけ残っている: YouTube公開→public、Discord限定→allowed、OK→allowed、NG→forbidden
- 2 つ以上残っている（未回答）・判別できない: allowed（撮るが公開しない、安全側）
- 【動画撮影】の行が無い: 変更しない（public のまま）

判定は純粋関数 ``policy_from_additional_info`` にまとめ、アプリのコードは import しない
（migration は後から変わるコードに依存させない）。
"""

import re
import unicodedata

from django.db import migrations

PUBLIC = 'public'
ALLOWED = 'allowed'
FORBIDDEN = 'forbidden'
FALLBACK_POLICY = ALLOWED
UPDATE_CHUNK_SIZE = 500

# 全角・大文字小文字の表記ゆれは NFKC + casefold で吸収してから照合する
RECORDING_MARKER = re.compile(r'【\s*動画撮影\s*】')
OPTION_PATTERNS = (
    (re.compile(r'youtube\s*公開'), PUBLIC),
    (re.compile(r'discord\s*限定'), ALLOWED),
    # 英字の途中（book, using など）には反応させない
    (re.compile(r'(?<![a-z])ok(?![a-z])'), ALLOWED),
    (re.compile(r'(?<![a-z])ng(?![a-z])'), FORBIDDEN),
)


def _recording_answer(text):
    """【動画撮影】の回答部分を返す。行が無ければ None。

    回答は見出しの後ろから次の【 または行末まで。見出しの行が空で
    次の行に書かれている場合も拾う（次の【 より前の最初の空でない行）。
    """
    normalized = unicodedata.normalize('NFKC', text).casefold()
    match = RECORDING_MARKER.search(normalized)
    if match is None:
        return None
    section = normalized[match.end():].split('【', 1)[0]
    for line in section.splitlines():
        if line.strip():
            return line
    return ''


def policy_from_additional_info(text):
    """自由記述から撮影の扱いを決める。【動画撮影】の行が無ければ None（変更しない）。"""
    if not text:
        return None
    answer = _recording_answer(text)
    if answer is None:
        return None
    found = [policy for pattern, policy in OPTION_PATTERNS if pattern.search(answer)]
    if len(found) == 1:
        return found[0]
    return FALLBACK_POLICY


def backfill_recording_policy(apps, schema_editor):
    EventDetail = apps.get_model('event', 'EventDetail')
    candidates = EventDetail._base_manager.filter(
        additional_info__contains='動画撮影',
    ).values_list('pk', 'additional_info')

    pks_by_policy = {}
    for pk, additional_info in candidates.iterator():
        policy = policy_from_additional_info(additional_info)
        if policy is None or policy == PUBLIC:
            continue
        pks_by_policy.setdefault(policy, []).append(pk)

    for policy, pks in pks_by_policy.items():
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
