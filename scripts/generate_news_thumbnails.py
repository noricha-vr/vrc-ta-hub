#!/usr/bin/env python
"""お知らせ記事のサムネイル画像（1200×630 PNG）を生成する。

定義は `app/news/thumbnail_specs.py`、描画は `app/news/thumbnail_generator.py`。
Django は使わないので `setup_django()` は呼ばず、`app_dir()` で import パスだけ通す。

使い方（コンテナ内。`./scripts` は read-only、`./app` は書き込み可）:

    docker compose exec vrc-ta-hub python /scripts/generate_news_thumbnails.py --force

作り直す時は `thumbnail_specs.py` の filename の `-vN` を上げること
（static は manifest ハッシュ無しで CDN にキャッシュされるため）。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from _script_bootstrap import app_dir

logger = logging.getLogger(__name__)

# app_dir() からの出力先（= app/news/static/news/images/og）
OUTPUT_SUBDIR = Path("news") / "static" / "news" / "images" / "og"
# --help を Django import 前に組み立てるため、generator 側の同名定数を複製している
CATEGORY_JOB_PREFIX = "category:"


def _import_generator():
    """Django セットアップ無しで news の描画モジュールを import する。"""
    app_path = str(app_dir())
    if app_path not in sys.path:
        sys.path.insert(0, app_path)
    from news import thumbnail_generator

    return thumbnail_generator


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="お知らせサムネイルを生成する")
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help=f"対象を記事 slug / {CATEGORY_JOB_PREFIX}<slug> で絞る（複数指定可）",
    )
    parser.add_argument("--force", action="store_true", help="既存ファイルを上書きする")
    parser.add_argument(
        "--out",
        default=None,
        help="出力ディレクトリ（既定: app/news/static/news/images/og）",
    )
    parser.add_argument("--dry-run", action="store_true", help="生成せず対象だけ表示する")
    return parser.parse_args(argv)


def generate_thumbnails(
    out_dir: Path,
    only: list[str] | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> int:
    """サムネイルを生成する。戻り値は exit code。"""
    generator = _import_generator()
    jobs = generator.build_jobs()
    if only:
        jobs = [job for job in jobs if job.name in set(only)]
        if not jobs:
            logger.error("--only に一致する対象がありません: %s", ", ".join(only))
            return 1

    written = 0
    for job in jobs:
        destination = out_dir / job.filename
        if destination.exists() and not force:
            logger.info("skip（既存）: %s", destination)
            continue
        if dry_run:
            logger.info("dry-run: %s -> %s", job.name, destination)
            continue
        image = generator.render_thumbnail(
            title=job.title,
            accent=job.accent,
            chip_label=job.chip_label,
            max_font_size=job.max_font_size,
        )
        generator.save_thumbnail(image, destination)
        written += 1
        logger.info("生成: %s (%d bytes)", destination, destination.stat().st_size)

    logger.info("完了: %d 件生成 / 対象 %d 件", written, len(jobs))
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI エントリポイント。戻り値は exit code。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    args = _parse_args(argv)
    out_dir = Path(args.out) if args.out else app_dir() / OUTPUT_SUBDIR
    try:
        return generate_thumbnails(
            out_dir=out_dir,
            only=args.only,
            force=args.force,
            dry_run=args.dry_run,
        )
    except Exception:
        logger.exception("サムネイル生成に失敗しました")
        return 1


if __name__ == "__main__":
    sys.exit(main())
