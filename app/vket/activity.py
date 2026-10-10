"""申込み画面の保存後に、変更内容を運営の通知先へ送る。"""
from __future__ import annotations

import logging
import re
from functools import partial

from django.db import transaction

from website.discord_webhook import post_discord_webhook

from .models import VketCollaboration, VketParticipation

logger = logging.getLogger(__name__)


def activity_snapshot(participation: VketParticipation | None) -> dict | None:
    """更新日時を含めず、実際の入力・日程・発表の変化を比較できる値を返す"""
    if participation is None:
        return None
    return {
        'lifecycle': participation.lifecycle,
        'is_applied': participation.progress != VketParticipation.Progress.NOT_APPLIED,
        'requested_schedule': (
            participation.requested_date, participation.requested_start_time,
            participation.requested_duration,
        ),
        'schedule': (
            participation.effective_date, participation.effective_start_time,
            participation.effective_duration,
        ),
        'organizer_note': participation.organizer_note,
        'lt_slot_minutes': participation.lt_slot_minutes,
        'presentations': {
            p.pk: (p.speaker, p.theme, p.confirmed_start_time or p.requested_start_time, p.duration, p.status)
            for p in participation.presentations.all()
        },
    }


def _operations(before: dict | None, after: dict) -> list[str]:
    """一度の保存で起きた操作をまとめる"""
    if before is None or (not before['is_applied'] and after['is_applied']):
        return ['新規の申込み']
    operations = []
    if before['lifecycle'] != after['lifecycle']:
        operations.append('辞退' if after['lifecycle'] == VketParticipation.Lifecycle.WITHDRAWN else '参加状態の変更')
    if (before['requested_schedule'], before['schedule']) != (after['requested_schedule'], after['schedule']):
        operations.append('日程の変更')
    old, new = before['presentations'], after['presentations']
    if new.keys() - old.keys():
        operations.append('発表の追加')
    if any(old[pk] != new[pk] for pk in old.keys() & new.keys()):
        operations.append('発表の変更')
    if old.keys() - new.keys():
        operations.append('発表の取り下げ')
    return operations or ['申込み情報の変更']


def _escape_markdown(value: str) -> str:
    """自由入力の書式記号をエスケープし、リンクや強調として解釈させない"""
    return re.sub(r'([\\*_~`|>\[\]()#-])', r'\\\1', value)


def _short(value: str, limit: int = 80) -> str:
    """通知用の自由入力を一行に省略してから、書式記号をエスケープする"""
    value = ' '.join(value.split()) or '未入力'
    value = value if len(value) <= limit else value[:limit - 1] + '…'
    return _escape_markdown(value)


def _bounded_lines(lines: list[str], limit: int) -> str:
    """行を途中で切らずに上限へ収め、残りの件数を末尾に示す"""
    included = []
    length = 0
    for index, line in enumerate(lines):
        remaining = len(lines) - index - 1
        suffix = f'\nほか {remaining} 件' if remaining else ''
        added_length = len(line) + bool(included)
        if length + added_length + len(suffix) > limit:
            return '\n'.join(included + [f'ほか {len(lines) - index} 件'])
        included.append(line)
        length += added_length
    return '\n'.join(included)


def notify_activity(
    collaboration: VketCollaboration, community_name: str,
    before: dict | None, after: dict, pair_lines: list[str],
) -> None:
    """設定が有効で変化がある時だけ、コミット後に一度通知する"""
    if before == after:
        return
    settings = collaboration.settings_json
    url = settings.get('activity_webhook_url') if isinstance(settings, dict) else None
    if not isinstance(url, str) or not re.fullmatch(r'https://discord\.com/api/webhooks/[0-9]+/[A-Za-z0-9_-]+', url):
        return
    day, start, duration = after['schedule']
    schedule = f'{day:%Y/%m/%d}' if day else '日付未定'
    schedule += f' {start:%H:%M}' if start else ' 時刻未定'
    schedule += f'（{duration}分）' if duration else ''
    content = (
        f"**{_short(community_name, 200)}**\n"
        f"操作: {'／'.join(_operations(before, after))}\n"
        f"参加日程: {schedule}\n"
        f"参加状態: {dict(VketParticipation.Lifecycle.choices).get(after['lifecycle'], after['lifecycle'])}"
    )
    lines = [
        f"- {_short(speaker)} / {_short(theme)} / "
        + (f'{start:%H:%M}' if start else '時刻未定') + f'（{duration}分）'
        for speaker, theme, start, duration, status in after['presentations'].values()
    ]
    # 各欄と埋め込み全体（6000文字）の上限に収め、収まらない行は件数だけ示す。
    embeds = [{'title': '現在の発表一覧', 'description': _bounded_lines(lines, 4096) or '発表なし'}]
    if pair_lines:
        warning = _bounded_lines([f'- {_escape_markdown(line)}' for line in pair_lines], 1800)
        embeds.append({'title': '発表時間の重なり', 'description': warning})
    payload = {'content': content, 'embeds': embeds, 'allowed_mentions': {'parse': []}}
    transaction.on_commit(partial(_send_activity, url, payload, collaboration.pk))


def _send_activity(url: str, payload: dict, collaboration_id: int) -> None:
    """通知失敗は保存に影響させず、URL・例外本文をログに出さない"""
    try:
        post_discord_webhook(url, payload)
    except Exception as exc:
        logger.warning(
            'Vket申込み通知に失敗',
            extra={'collaboration_id': collaboration_id, 'result': 'failed', 'error_type': type(exc).__name__},
        )
    else:
        logger.info('Vket申込み通知を送信', extra={'collaboration_id': collaboration_id, 'result': 'sent'})
