#!/usr/bin/env python
"""fixture の Markdown からお知らせ記事を作成する汎用スクリプト。

`app/news/fixtures/<name>.md` のフロントマター（`---` で囲まれた `key: value` 行）を
読み取り、`news.Post` を 1 件作成する。同じ slug の記事が既にあれば何もせず 0 で終了する。

使い方（コンテナ内。`./scripts` は read-only マウント）:

    python /scripts/create_news_post.py --fixture 2026-09-17-presentation-list-redesign.md
    python /scripts/create_news_post.py --fixture xxx.md --draft  # 非公開で作成

カテゴリ不在・fixture 不在・フロントマター不正はいずれも非ゼロ終了する。
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from _script_bootstrap import app_dir, setup_django

logger = logging.getLogger(__name__)

# fixture の置き場所（app_dir() からの相対パス）
FIXTURES_SUBDIR = Path("news") / "fixtures"
# フロントマターの区切り行
FRONT_MATTER_DELIMITER = "---"
# フロントマターに必須のキー
REQUIRED_KEYS = ("title", "slug", "category")
# 取り込む任意キー
OPTIONAL_KEYS = ("meta_description",)
# 値を囲めるクォート
QUOTE_CHARS = "\"'"


class FrontMatterError(ValueError):
    """フロントマターの形式が不正なときに送出する。"""


def _strip_quotes(value: str) -> str:
    """前後を同じクォートで囲まれていれば外す。"""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in QUOTE_CHARS:
        return value[1:-1]
    return value


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """フロントマターと本文に分割する。

    PyYAML には依存せず、`key: value` の1行形式だけを解釈する。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != FRONT_MATTER_DELIMITER:
        raise FrontMatterError("フロントマターの開始行（---）がありません")

    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == FRONT_MATTER_DELIMITER:
            end_index = index
            break
    if end_index is None:
        raise FrontMatterError("フロントマターの終了行（---）がありません")

    metadata: dict[str, str] = {}
    for line in lines[1:end_index]:
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        if not separator:
            raise FrontMatterError(f"`key: value` 形式でない行があります: {line!r}")
        metadata[key.strip()] = _strip_quotes(value.strip())

    missing = [key for key in REQUIRED_KEYS if not metadata.get(key)]
    if missing:
        raise FrontMatterError(f"必須キーがありません: {', '.join(missing)}")

    body = "\n".join(lines[end_index + 1:]).lstrip("\n")
    if not body.strip():
        raise FrontMatterError("本文が空です")
    return metadata, body


def fixture_path(fixture_name: str) -> Path:
    """fixture 名から絶対パスを組み立てる（ディレクトリ外への脱出を禁止）。"""
    if Path(fixture_name).name != fixture_name:
        raise FrontMatterError(f"fixture はファイル名のみ指定してください: {fixture_name!r}")
    return app_dir() / FIXTURES_SUBDIR / fixture_name


def load_fixture(fixture_name: str) -> tuple[dict[str, str], str]:
    """fixture を読んでフロントマターと本文を返す。

    形式不正は FrontMatterError、読み込み失敗は OSError を送出する。
    """
    return parse_front_matter(fixture_path(fixture_name).read_text(encoding="utf-8"))


def create_news_post(fixture_name: str, draft: bool = False) -> int:
    """fixture からお知らせ記事を作成する。戻り値は exit code。"""
    from news.models import Category, Post

    try:
        metadata, body_markdown = load_fixture(fixture_name)
    except FrontMatterError as e:
        logger.error("fixture が不正です（%s）: %s", fixture_name, e)
        return 1
    except OSError as e:
        logger.error("fixture の読み込みに失敗（%s）: %s", fixture_name, e)
        return 1

    slug = metadata["slug"]

    # 既存記事があるのは正常系（冪等に再実行できる）
    if Post.objects.filter(slug=slug).exists():
        logger.info("記事は既に存在します: %s", slug)
        return 0

    try:
        category = Category.objects.get(slug=metadata["category"])
    except Category.DoesNotExist:
        logger.error("カテゴリーが見つかりません: %s", metadata["category"])
        return 1

    post = Post.objects.create(
        title=metadata["title"],
        slug=slug,
        body_markdown=body_markdown,
        meta_description=metadata.get(OPTIONAL_KEYS[0], ""),
        category=category,
        is_published=not draft,
        published_at=None if draft else datetime.now(timezone.utc),
    )
    state = "下書き" if draft else "公開"
    logger.info("記事を作成しました: %s (%s, %s)", post.title, post.slug, state)
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="fixture からお知らせ記事を作成する")
    parser.add_argument(
        "--fixture",
        required=True,
        help="app/news/fixtures/ 内の Markdown ファイル名",
    )
    parser.add_argument(
        "--draft",
        action="store_true",
        help="非公開（下書き）として作成する",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI エントリポイント。戻り値は exit code。"""
    args = _parse_args(argv)
    setup_django()
    try:
        return create_news_post(fixture_name=args.fixture, draft=args.draft)
    except Exception:
        logger.exception("お知らせ記事の作成に失敗しました")
        return 1


if __name__ == "__main__":
    sys.exit(main())
