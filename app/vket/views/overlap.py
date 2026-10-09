"""運営の確定・公開同期で、確定済みの枠との重なりを承知してもらう確認画面。"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from django.contrib import messages
from django.shortcuts import render

from ..models import VketCollaboration
from ..schedule import ALLOW_OVERLAP_FIELD, OVERLAP_SIGNATURE_FIELD

logger = logging.getLogger(__name__)

_NOT_CARRIED = {'csrfmiddlewaretoken', ALLOW_OVERLAP_FIELD, OVERLAP_SIGNATURE_FIELD}


@dataclass(frozen=True)
class OverlapConfirmation:
    """確認画面に出す内容"""

    title: str
    lead: str
    checkbox_label: str
    submit_label: str
    action_url: str


def render_overlap_confirmation(
    request,
    collaboration: VketCollaboration,
    confirmation: OverlapConfirmation,
    pair_lines: list[str],
    signature: str,
):
    """重なっている組と承知のチェックを出し、元の入力をそのまま送り直せる画面を返す"""
    carried = [
        (name, value)
        for name, values in request.POST.lists()
        if name not in _NOT_CARRIED
        for value in values
    ]
    # 承知のチェックを付けて送ったのに組が変わっていた時は、新しい組で再確認してもらう
    changed = request.POST.get(ALLOW_OVERLAP_FIELD) == '1'
    return render(
        request,
        'vket/manage_overlap_confirm.html',
        {
            'collaboration': collaboration,
            'confirmation': confirmation,
            'pair_lines': pair_lines,
            'signature': signature,
            'carried_fields': carried,
            'pairs_changed': changed,
            'allow_overlap_field': ALLOW_OVERLAP_FIELD,
            'signature_field': OVERLAP_SIGNATURE_FIELD,
        },
    )


def record_acknowledged_overlap(request, action: str, pair_lines: list[str], extra: dict) -> None:
    """承知して進めた重なりの組を、ログと画面のメッセージに残す"""
    logger.warning(
        'Vketコラボ: 重なりを承知で%s', action,
        extra={'user_id': request.user.id, 'overlap_pairs': pair_lines, **extra},
    )
    messages.warning(request, f'次の重なりを承知で{action}しました: ' + ' / '.join(pair_lines))
