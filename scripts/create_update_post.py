#!/usr/bin/env python
"""アップデート記事を作成するスクリプト

カテゴリ不在・fixture 不在などの失敗時は非ゼロ終了する。
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from _script_bootstrap import app_dir, setup_django

logger = logging.getLogger(__name__)

MARKDOWN_FILENAME = "2025-01-10-ui-improvements.md"


def create_update_post() -> int:
    """アップデート記事を作成する。戻り値は exit code。"""
    from news.models import Category, Post

    try:
        update_category = Category.objects.get(slug='update')
    except Category.DoesNotExist:
        logger.error("アップデートカテゴリーが見つかりません")
        return 1

    title = "サイトのUI/UX改善とGoogleカレンダー連携機能を追加しました"
    slug = "2025-01-10-ui-improvements"

    # 既存記事があるのは正常系（冪等に再実行できる）
    if Post.objects.filter(slug=slug).exists():
        logger.info("記事は既に存在します: %s", slug)
        return 0

    markdown_path = Path(app_dir()) / "news" / "fixtures" / MARKDOWN_FILENAME
    try:
        body_markdown = markdown_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.error("Markdownファイルの読み込みに失敗: %s: %s", markdown_path, e)
        return 1

    post = Post.objects.create(
        title=title,
        slug=slug,
        body_markdown=body_markdown,
        meta_description="フッターデザイン刷新、Googleカレンダー連携機能追加、ナビゲーション最適化などサイトのUI/UX改善を実施しました。",
        category=update_category,
        is_published=True,
        published_at=datetime.now(timezone.utc)
    )
    logger.info("記事を作成しました: %s", post.title)
    return 0


def main() -> int:
    setup_django()
    try:
        return create_update_post()
    except Exception:
        logger.exception("アップデート記事の作成に失敗しました")
        return 1


if __name__ == '__main__':
    sys.exit(main())
