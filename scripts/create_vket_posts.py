#!/usr/bin/env python
"""Vket関連の記事を作成するスクリプト

カテゴリ不在・fixture 不在などの失敗時は非ゼロ終了する。
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from _script_bootstrap import app_dir, setup_django

logger = logging.getLogger(__name__)


def _posts_data(fixtures_dir: Path) -> list[dict]:
    return [
        {
            'title': 'VRC技術・学術系イベントHUB × Vketステージ コラボ【Vket技術学術WEEK】開催決定！',
            'slug': '2025-07-04-vket-week-announcement',
            'markdown_file': fixtures_dir / '2025-07-04-vket-week-announcement.md',
            'meta_description': 'VirtualMarketとのコラボで16日間連続・20団体が登壇するVket技術学術WEEKの開催が決定！',
            'published_at': datetime(2025, 7, 4, 19, 52, tzinfo=timezone.utc),  # 画像のツイート時刻
        },
        {
            'title': 'Vket技術学術WEEK 動画アーカイブまとめ',
            'slug': '2025-01-10-vket-week-videos',
            'markdown_file': fixtures_dir / '2025-01-10-vket-week-videos.md',
            'meta_description': '2025年7月12日〜27日開催のVket技術学術WEEK全20団体の発表動画アーカイブまとめ',
            'published_at': datetime.now(timezone.utc),  # 今日の日付
        }
    ]


def create_vket_posts() -> int:
    """Vket関連の記事を作成する。戻り値は exit code。"""
    from news.models import Category, Post

    try:
        activity_category = Category.objects.get(slug='activity')
    except Category.DoesNotExist:
        logger.error("活動履歴カテゴリーが見つかりません")
        return 1

    failed = 0
    for data in _posts_data(Path(app_dir()) / "news" / "fixtures"):
        # 既存記事があるのは正常系（冪等に再実行できる）
        if Post.objects.filter(slug=data['slug']).exists():
            logger.info("記事は既に存在します: %s", data['slug'])
            continue

        try:
            body_markdown = Path(data['markdown_file']).read_text(encoding="utf-8")
        except OSError as e:
            logger.error("ファイルの読み込みに失敗: %s: %s", data['markdown_file'], e)
            failed += 1
            continue

        post = Post.objects.create(
            title=data['title'],
            slug=data['slug'],
            body_markdown=body_markdown,
            meta_description=data['meta_description'],
            category=activity_category,
            is_published=True,
            published_at=data['published_at']
        )
        logger.info("記事を作成しました: %s", post.title)

    if failed:
        logger.error("Vket関連記事の作成に失敗: %d件", failed)
        return 1

    logger.info("Vket関連記事の作成が完了しました")
    return 0


def main() -> int:
    setup_django()
    try:
        return create_vket_posts()
    except Exception:
        logger.exception("Vket関連記事の作成に失敗しました")
        return 1


if __name__ == '__main__':
    sys.exit(main())
