#!/usr/bin/env python
"""本文内のH1タグをH2に変換するスクリプト

EventDetailのcontents内にある # をすべて ## に変換する。
失敗時は非ゼロ終了して自動化から検知できるようにする。
"""
from __future__ import annotations

import logging
import sys

from _script_bootstrap import setup_django

logger = logging.getLogger(__name__)


def fix_inner_h1_tags() -> int:
    """EventDetailの本文内のH1をH2に変換する。戻り値は exit code。"""
    from event.models import EventDetail

    logger.info("=== 本文内のH1タグ修正開始 ===")

    fixed_count = 0
    event_details = EventDetail.objects.exclude(contents='').exclude(contents__isnull=True)

    for ed in event_details:
        if not ed.contents:
            continue

        lines = ed.contents.split('\n')
        new_lines = []
        has_h1 = False

        for line in lines:
            # 行頭の # で始まる行（H1）を検出
            if line.strip().startswith('# '):
                # H1をH2に変換（# を ## に）
                new_line = '#' + line.strip()
                new_lines.append(new_line)
                has_h1 = True
                if fixed_count == 0:  # 最初の1件だけ詳細を表示
                    logger.info("変換例: %s -> %s", line[:50], new_line[:50])
            else:
                new_lines.append(line)

        if has_h1:
            ed.contents = '\n'.join(new_lines)
            ed.save()
            logger.info("Fixed EventDetail ID %s: %s", ed.id, ed.title[:30])
            fixed_count += 1

    logger.info("EventDetail: %d件の本文内H1をH2に変換", fixed_count)
    logger.info("=== 修正完了 ===")
    return 0


def main() -> int:
    setup_django()
    try:
        return fix_inner_h1_tags()
    except Exception:
        logger.exception("本文内H1タグの変換に失敗しました")
        return 1


if __name__ == '__main__':
    sys.exit(main())
