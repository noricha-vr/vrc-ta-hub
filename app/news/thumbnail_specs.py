"""お知らせ記事の生成サムネイル定義。

`scripts/generate_news_thumbnails.py`（生成）と `news.models`（配信）が共有する
唯一の定義元。Django に依存しないので、Django セットアップ無しで import できる。

運用ルール:
- 画像は `app/news/static/news/images/og/` に置き、static は manifest ハッシュ無しで
  CDN にキャッシュされる。作り直す時は必ず filename の `-vN` を上げる。
- DB で記事タイトルを改題しても画像は変わらない。ここの `title` を直し、
  filename を `-v2` に上げて再生成する。
"""
from __future__ import annotations

from dataclasses import dataclass

# static ディレクトリ（STATIC_URL からの相対パス）
STATIC_SUBDIR = "news/images/og"

# カテゴリ別のアクセントカラー（サイト primary と補色）
DEFAULT_ACCENT = "#1B7DE6"
CATEGORY_ACCENTS: dict[str, str] = {
    "update": DEFAULT_ACCENT,
    "activity": "#18A38F",
}

# チップ・既定画像に描くカテゴリ表示名（news/migrations の Category.name と対応）
CATEGORY_LABELS: dict[str, str] = {
    "update": "アップデート",
    "activity": "活動履歴",
}


@dataclass(frozen=True)
class PostThumbnailSpec:
    """1 記事ぶんの生成サムネイル定義。"""

    slug: str
    title: str
    category_slug: str
    filename: str


@dataclass(frozen=True)
class CategoryThumbnailSpec:
    """カテゴリ既定サムネイル（slug 個別指定が無い記事のフォールバック）の定義。"""

    category_slug: str
    label: str
    filename: str


POST_THUMBNAIL_SPECS: tuple[PostThumbnailSpec, ...] = (
    PostThumbnailSpec(
        slug="2025-01-10-ui-improvements",
        title="サイトのUI/UX改善とGoogleカレンダー連携機能を追加しました",
        category_slug="update",
        filename="2025-01-10-ui-improvements-v1.png",
    ),
    PostThumbnailSpec(
        slug="fix-google-calendar-sync-bug",
        title="Googleカレンダー同期の不具合を修正しました",
        category_slug="update",
        filename="fix-google-calendar-sync-bug-v1.png",
    ),
    PostThumbnailSpec(
        slug="change-day-cutoff-to-4am",
        title="開催日程とトップページの切替時刻を午前4時に変更",
        category_slug="update",
        filename="change-day-cutoff-to-4am-v1.png",
    ),
    PostThumbnailSpec(
        slug="event-info-asset",
        title="技術・学術系イベント情報アセット",
        category_slug="activity",
        filename="event-info-asset-v1.png",
    ),
    PostThumbnailSpec(
        slug="event-guide-book",
        title="VRC 技術・学術イベントガイド 本",
        category_slug="activity",
        filename="event-guide-book-v1.png",
    ),
    PostThumbnailSpec(
        slug="website-management",
        title="Webサイトの制作・運営",
        category_slug="activity",
        filename="website-management-v1.png",
    ),
    PostThumbnailSpec(
        slug="2025-07-04-vket-week-announcement",
        title=(
            "VRC技術・学術系イベントHUB × Vketステージ コラボ"
            "【Vket技術学術WEEK】開催決定！"
        ),
        category_slug="activity",
        filename="2025-07-04-vket-week-announcement-v1.png",
    ),
)


CATEGORY_THUMBNAIL_SPECS: tuple[CategoryThumbnailSpec, ...] = (
    CategoryThumbnailSpec(
        category_slug="update",
        label=CATEGORY_LABELS["update"],
        filename="category-update-v1.png",
    ),
    CategoryThumbnailSpec(
        category_slug="activity",
        label=CATEGORY_LABELS["activity"],
        filename="category-activity-v1.png",
    ),
)


def static_path(filename: str) -> str:
    """static 相対パス（`news/images/og/xxx.png`）を返す。"""
    return f"{STATIC_SUBDIR}/{filename}"


def accent_for(category_slug: str) -> str:
    """カテゴリのアクセントカラーを返す（未定義は既定色）。"""
    return CATEGORY_ACCENTS.get(category_slug, DEFAULT_ACCENT)


def label_for(category_slug: str) -> str:
    """カテゴリ表示名を返す（未定義は slug をそのまま返す）。"""
    return CATEGORY_LABELS.get(category_slug, category_slug)


def build_slug_map() -> dict[str, str]:
    """記事 slug -> static 相対パス のマップを返す。"""
    return {spec.slug: static_path(spec.filename) for spec in POST_THUMBNAIL_SPECS}


def build_category_map() -> dict[str, str]:
    """カテゴリ slug -> static 相対パス のマップを返す。"""
    return {
        spec.category_slug: static_path(spec.filename)
        for spec in CATEGORY_THUMBNAIL_SPECS
    }
