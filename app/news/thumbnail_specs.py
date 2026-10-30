"""お知らせ記事のサムネイル定義。

`scripts/generate_news_thumbnails.py`（生成）と `news.models`（配信）が共有する
唯一の定義元。Django に依存しないので、Django セットアップ無しで import できる。

運用ルール:
- 画像は `app/news/static/news/images/og/` に置き、static は manifest ハッシュ無しで
  CDN にキャッシュされる。作り直す時は必ず filename の `-vN` を上げる。
- リッチ版と文字だけの生成定義を分離し、文字画像の再生成でリッチ版を上書きしない。
- DB で記事タイトルを改題しても画像は変わらない。画像も直し、filename の版を上げる。
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
    """文字だけの生成器で描く 1 記事ぶんの定義。"""

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


# 文字だけの生成器が描く記事。リッチ版はここへ追加しない。
POST_THUMBNAIL_SPECS: tuple[PostThumbnailSpec, ...] = ()


@dataclass(frozen=True)
class RichPostThumbnailSpec:
    """別工程で制作したリッチ版。文字だけの生成器では描画しない。"""

    slug: str
    filename: str
    prefer_over_upload: bool = False


RICH_POST_THUMBNAIL_SPECS: tuple[RichPostThumbnailSpec, ...] = (
    RichPostThumbnailSpec("2025-01-10-ui-improvements", "2025-01-10-ui-improvements-v2.jpg"),
    RichPostThumbnailSpec("fix-google-calendar-sync-bug", "fix-google-calendar-sync-bug-v2.jpg"),
    RichPostThumbnailSpec("change-day-cutoff-to-4am", "change-day-cutoff-to-4am-v2.jpg"),
    RichPostThumbnailSpec("event-info-asset", "event-info-asset-v2.jpg"),
    RichPostThumbnailSpec("event-guide-book", "event-guide-book-v2.jpg"),
    RichPostThumbnailSpec("website-management", "website-management-v2.jpg"),
    RichPostThumbnailSpec("2025-07-04-vket-week-announcement", "2025-07-04-vket-week-announcement-v2.jpg"),
    RichPostThumbnailSpec("2026-10-11-auto-recording-beta", "2026-10-11-auto-recording-beta-v3.jpg"),
    RichPostThumbnailSpec("2026-09-17-presentation-list-redesign", "2026-09-17-presentation-list-redesign-v3.jpg"),
    RichPostThumbnailSpec("vket-2026-summer", "vket-2026-summer-video-archive-v2.jpg"),
    RichPostThumbnailSpec("2026-10-31-misskey-account", "2026-10-31-misskey-account-v1.jpg", prefer_over_upload=True),
    RichPostThumbnailSpec("vket-2025-summer", "vket-2025-summer-v1.jpg", prefer_over_upload=True),
    RichPostThumbnailSpec("vket-2025-winter", "vket-2025-winter-v1.jpg", prefer_over_upload=True),
    RichPostThumbnailSpec("vrchat-nyumon-2025", "vrchat-nyumon-2025-v1.jpg", prefer_over_upload=True),
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
    """static 相対パス（`news/images/og/xxx.jpg` など）を返す。"""
    return f"{STATIC_SUBDIR}/{filename}"


def accent_for(category_slug: str) -> str:
    """カテゴリのアクセントカラーを返す（未定義は既定色）。"""
    return CATEGORY_ACCENTS.get(category_slug, DEFAULT_ACCENT)


def label_for(category_slug: str) -> str:
    """カテゴリ表示名を返す（未定義は slug をそのまま返す）。"""
    return CATEGORY_LABELS.get(category_slug, category_slug)


def build_slug_map() -> dict[str, str]:
    """記事 slug -> static 相対パス のマップを返す。"""
    return {
        spec.slug: static_path(spec.filename)
        for spec in (*POST_THUMBNAIL_SPECS, *RICH_POST_THUMBNAIL_SPECS)
    }


def build_category_map() -> dict[str, str]:
    """カテゴリ slug -> static 相対パス のマップを返す。"""
    return {
        spec.category_slug: static_path(spec.filename)
        for spec in CATEGORY_THUMBNAIL_SPECS
    }
