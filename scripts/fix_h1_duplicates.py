#!/usr/bin/env python
"""H1タグの重複を修正するスクリプト

NewsとEventDetailの本文から最初の # 行を削除する。
失敗時は非ゼロ終了して自動化から検知できるようにする。
"""
from __future__ import annotations

import logging
import sys

from _script_bootstrap import setup_django

logger = logging.getLogger(__name__)


def fix_h1_duplicates() -> int:
    """NewsとEventDetailのH1重複を修正する。戻り値は exit code。"""
    from event.models import EventDetail
    from news.models import Post

    logger.info("=== H1重複修正開始 ===")

    # News記事のH1削除
    logger.info("News記事の修正...")
    news_fixed = 0
    for post in Post.objects.all():
        lines = post.body_markdown.split('\n')
        if lines and lines[0].strip().startswith('# '):
            original_first_line = lines[0]
            post.body_markdown = '\n'.join(lines[1:]).lstrip()
            post.save()
            logger.info("Fixed News: %s", post.slug)
            logger.info("  削除した行: %s", original_first_line[:50])
            news_fixed += 1

    logger.info("News記事: %d件修正", news_fixed)

    # EventDetailのH1削除
    logger.info("EventDetailの修正...")
    event_fixed = 0
    event_details = EventDetail.objects.exclude(contents='').exclude(contents__isnull=True)
    for ed in event_details:
        if ed.contents and ed.contents.strip().startswith('# '):
            lines = ed.contents.split('\n')
            original_first_line = lines[0]
            ed.contents = '\n'.join(lines[1:]).lstrip()
            ed.save()
            logger.info("Fixed EventDetail ID %s: %s", ed.id, ed.title[:30])
            logger.info("  削除した行: %s", original_first_line[:50])
            event_fixed += 1

    logger.info("EventDetail: %d件修正", event_fixed)
    logger.info("=== 修正完了 === 合計: %d件", news_fixed + event_fixed)
    return 0


def main() -> int:
    setup_django()
    try:
        return fix_h1_duplicates()
    except Exception:
        logger.exception("H1重複の修正に失敗しました")
        return 1


if __name__ == '__main__':
    sys.exit(main())
