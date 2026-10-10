"""発表時間の重なりを保存後の警告とログに残す。"""
from __future__ import annotations

import logging

from django.contrib import messages

logger = logging.getLogger(__name__)


def warn_overlap(request, action: str, pair_lines: list[str], extra: dict) -> None:
    """重なりがある時だけ警告する。操作を止めず、ログには件数を残す"""
    if not pair_lines:
        return
    logger.warning(
        'Vketコラボ: 発表時間の重なり',
        extra={'action': action, 'overlap_count': len(pair_lines), **extra},
    )
    messages.warning(request, '発表時間が重なっています: ' + ' / '.join(pair_lines))
