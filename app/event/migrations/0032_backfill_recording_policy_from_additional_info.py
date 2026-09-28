"""既存の発表申請の自由記述（additional_info）にある【動画撮影】の回答から撮影の扱いを決める。

発表申請テンプレートの旧既定文には「【動画撮影】YouTube公開 / Discord限定 / OK / NG」の行があり、
発表者は不要な選択肢を消して回答していた。その回答を recording_policy に移す。

判定の規則（迷ったら撮らない・公開しない側へ倒す）:
1. 4 つの選択肢のうち 3 つ以上がそのまま残っている（テンプレのまま未回答）→ 選択肢を
   1 つずつ取り除いた残りに拒否を表す語があれば forbidden、無ければ allowed
2. 拒否を表す語（NG、不可、禁止、しない、ダメ、お断り、×、NO など）がある → forbidden
3. 選択肢が 1 つだけ → YouTube公開→public、Discord限定→allowed、OK→allowed
4. それ以外 → allowed
【動画撮影】の行が無い発表は public のまま。

判定は event/recording_policy_answers.py（デプロイ後の当て直しコマンドが使う）の
この時点の写しで、migration は後から変わるアプリのコードを import しない。
"""

import re
import unicodedata

from django.db import migrations

UPDATE_CHUNK_SIZE = 500

PUBLIC = 'public'
ALLOWED = 'allowed'
FORBIDDEN = 'forbidden'
FALLBACK_POLICY = ALLOWED
UNANSWERED_OPTION_COUNT = 3

# 候補を DB から取る時の絞り込み語（判定そのものは下の正規表現で行う）
RECORDING_KEYWORD = '動画撮影'

# 全角・大文字小文字の表記ゆれは NFKC + casefold で吸収してから照合する
RECORDING_MARKER = re.compile(r'【\s*動画撮影\s*】')
_NOT_IN_WORD = r'(?<![a-z]){}(?![a-z])'
OPTION_PATTERNS = (
    (re.compile(r'youtube\s*公開'), PUBLIC),
    (re.compile(r'discord\s*限定'), ALLOWED),
    (re.compile(_NOT_IN_WORD.format('ok')), ALLOWED),
    (re.compile(_NOT_IN_WORD.format('ng')), FORBIDDEN),
)
REFUSAL_PATTERN = re.compile(
    '|'.join((
        _NOT_IN_WORD.format('ng'),
        _NOT_IN_WORD.format('no'),
        '不可', '禁止', 'しない', 'ダメ', 'だめ', 'お断り', '×', '✕',
    ))
)


def _recording_answer(text):
    """【動画撮影】の回答部分を返す。行が無ければ None。

    回答は見出しの直後から次の【（無ければ末尾）までの全行。
    次の行以降に書き足された回答も判定に含める。
    """
    normalized = unicodedata.normalize('NFKC', text).casefold()
    match = RECORDING_MARKER.search(normalized)
    if match is None:
        return None
    return normalized[match.end():].split('【', 1)[0]


def _without_options(answer):
    """回答から各選択肢を 1 回ずつ取り除く（テンプレの並びを消し、書き足した語だけ残す）。"""
    for pattern, _policy in OPTION_PATTERNS:
        answer = pattern.sub(' ', answer, count=1)
    return answer


def policy_from_additional_info(text):
    """自由記述から撮影の扱い（'public' / 'allowed' / 'forbidden'）を決める。

    【動画撮影】の行が無ければ None（変更しない）。
    """
    if not text:
        return None
    answer = _recording_answer(text)
    if answer is None:
        return None
    found = [policy for pattern, policy in OPTION_PATTERNS if pattern.search(answer)]
    if len(found) >= UNANSWERED_OPTION_COUNT:
        if REFUSAL_PATTERN.search(_without_options(answer)):
            return FORBIDDEN
        return FALLBACK_POLICY
    if REFUSAL_PATTERN.search(answer):
        return FORBIDDEN
    if len(found) == 1:
        return found[0]
    return FALLBACK_POLICY


def plan_policy_changes(rows):
    """(pk, 自由記述) の並びから、public 以外に変える発表を撮影の扱いごとにまとめる。

    Returns:
        ``{'forbidden': [pk, ...], 'allowed': [pk, ...]}``（変更の無い扱いはキーを持たない）
    """
    pks_by_policy = {}
    for pk, additional_info in rows:
        policy = policy_from_additional_info(additional_info)
        if policy is None or policy == PUBLIC:
            continue
        pks_by_policy.setdefault(policy, []).append(pk)
    return pks_by_policy


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
