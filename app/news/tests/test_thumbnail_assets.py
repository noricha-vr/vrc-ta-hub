"""生成サムネイル（static 画像）と定義の整合テスト。

画像が欠けた状態でデプロイすると一覧が壊れるため、マップの参照先が
実在し 1200×630 であることを保証する。
"""
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase, override_settings
from PIL import Image

from news.models import Post, STATIC_THUMBNAIL_BY_CATEGORY_SLUG, STATIC_THUMBNAIL_BY_SLUG
from news.thumbnail_generator import IMAGE_HEIGHT, IMAGE_WIDTH
from news.thumbnail_specs import (
    CATEGORY_ACCENTS,
    CATEGORY_THUMBNAIL_SPECS,
    POST_THUMBNAIL_SPECS,
    RICH_POST_THUMBNAIL_SPECS,
    build_category_map,
    build_slug_map,
)

EXPECTED_POST_SPEC_COUNT = 0
EXPECTED_RICH_FILES = {
    "2025-01-10-ui-improvements": "2025-01-10-ui-improvements-v2.jpg",
    "fix-google-calendar-sync-bug": "fix-google-calendar-sync-bug-v2.jpg",
    "change-day-cutoff-to-4am": "change-day-cutoff-to-4am-v2.jpg",
    "event-info-asset": "event-info-asset-v2.jpg",
    "event-guide-book": "event-guide-book-v2.jpg",
    "website-management": "website-management-v2.jpg",
    "2025-07-04-vket-week-announcement": "2025-07-04-vket-week-announcement-v2.jpg",
    "2026-10-11-auto-recording-beta": "2026-10-11-auto-recording-beta-v3.jpg",
    "2026-09-17-presentation-list-redesign": "2026-09-17-presentation-list-redesign-v3.jpg",
    "vket-2026-summer": "vket-2026-summer-video-archive-v2.jpg",
    "2026-10-31-misskey-account": "2026-10-31-misskey-account-v1.jpg",
    "vket-2025-summer": "vket-2025-summer-v1.jpg",
    "vket-2025-winter": "vket-2025-winter-v1.jpg",
    "vrchat-nyumon-2025": "vrchat-nyumon-2025-v1.jpg",
}
EXPECTED_UPLOAD_OVERRIDE_SLUGS = {
    "2026-10-31-misskey-account", "vket-2025-summer", "vket-2025-winter", "vrchat-nyumon-2025",
}
EXPECTED_CATEGORY_SPEC_COUNT = 2


class ThumbnailSpecTestCase(SimpleTestCase):
    """thumbnail_specs の定義そのもののテスト"""

    def test_post_spec_count(self):
        """記事サムネイルの定義数が想定どおり"""
        self.assertEqual(len(POST_THUMBNAIL_SPECS), EXPECTED_POST_SPEC_COUNT)

    def test_rich_specs_match_expected_files_and_overrides(self):
        """14 本のファイル名とアップロードより優先する 4 本を固定する"""
        self.assertEqual(
            {spec.slug: spec.filename for spec in RICH_POST_THUMBNAIL_SPECS},
            EXPECTED_RICH_FILES,
        )
        self.assertEqual(
            {spec.slug for spec in RICH_POST_THUMBNAIL_SPECS if spec.prefer_over_upload},
            EXPECTED_UPLOAD_OVERRIDE_SLUGS,
        )

    def test_category_spec_count(self):
        """カテゴリ既定サムネイルの定義数が想定どおり"""
        self.assertEqual(len(CATEGORY_THUMBNAIL_SPECS), EXPECTED_CATEGORY_SPEC_COUNT)

    def test_post_slugs_are_unique(self):
        """記事 slug が重複しない"""
        slugs = [spec.slug for spec in (*POST_THUMBNAIL_SPECS, *RICH_POST_THUMBNAIL_SPECS)]
        self.assertEqual(len(slugs), len(set(slugs)))

    def test_filenames_are_unique(self):
        """出力ファイル名が記事・カテゴリをまたいで重複しない"""
        filenames = [spec.filename for spec in (*POST_THUMBNAIL_SPECS, *RICH_POST_THUMBNAIL_SPECS)]
        filenames += [spec.filename for spec in CATEGORY_THUMBNAIL_SPECS]
        self.assertEqual(len(filenames), len(set(filenames)))

    def test_every_category_has_accent(self):
        """定義された全カテゴリにアクセントカラーがある"""
        for spec in CATEGORY_THUMBNAIL_SPECS:
            with self.subTest(category=spec.category_slug):
                self.assertIn(spec.category_slug, CATEGORY_ACCENTS)

    def test_post_categories_have_default_thumbnail(self):
        """記事のカテゴリには既定サムネイルも定義されている"""
        category_map = build_category_map()
        for spec in POST_THUMBNAIL_SPECS:
            with self.subTest(slug=spec.slug):
                self.assertIn(spec.category_slug, category_map)

    def test_models_map_includes_generated_slugs(self):
        """models のマップに生成画像の slug が取り込まれている"""
        for slug, path in build_slug_map().items():
            with self.subTest(slug=slug):
                self.assertEqual(STATIC_THUMBNAIL_BY_SLUG[slug], path)

    def test_models_category_map_matches_specs(self):
        """models のカテゴリマップが定義と一致する"""
        self.assertEqual(STATIC_THUMBNAIL_BY_CATEGORY_SLUG, build_category_map())


@override_settings(STATIC_URL="/static/", MEDIA_URL="/media/")
class RichThumbnailPriorityTestCase(SimpleTestCase):
    def test_all_rich_slugs_use_expected_static_without_category(self):
        """DB を参照せず全 14 slug の新画像を返す"""
        for slug, filename in EXPECTED_RICH_FILES.items():
            with self.subTest(slug=slug):
                post = Post(slug=slug)
                self.assertEqual(post.get_thumbnail_url(), f"/static/news/images/og/{filename}")
                self.assertEqual(post.static_thumbnail_path, f"news/images/og/{filename}")
                self.assertTrue(post.uses_static_thumbnail)
                self.assertTrue(post.has_detail_thumbnail)

    def test_only_explicit_slugs_override_uploads(self):
        """アップロード済みなら指定 4 本だけ static を優先し、他の 10 本は維持する"""
        for slug, filename in EXPECTED_RICH_FILES.items():
            with self.subTest(slug=slug):
                post = Post(slug=slug, thumbnail="news/uploaded.png")
                override = slug in EXPECTED_UPLOAD_OVERRIDE_SLUGS
                expected = f"/static/news/images/og/{filename}" if override else "/media/news/uploaded.png"
                self.assertEqual(post.get_thumbnail_url(), expected)
                self.assertEqual(post.uses_static_thumbnail, override)
                self.assertEqual(
                    post.static_thumbnail_path, f"news/images/og/{filename}" if override else None,
                )
                self.assertTrue(post.has_detail_thumbnail)
                self.assertEqual(post.thumbnail.name, "news/uploaded.png")


class ThumbnailAssetFileTestCase(SimpleTestCase):
    """マップが指す static ファイルの実在と寸法のテスト"""

    def _mapped_paths(self) -> list[str]:
        paths = list(STATIC_THUMBNAIL_BY_SLUG.values())
        paths += list(STATIC_THUMBNAIL_BY_CATEGORY_SLUG.values())
        return paths

    def test_all_mapped_files_exist(self):
        """全マップ値が staticfiles finders で見つかる"""
        for path in self._mapped_paths():
            with self.subTest(path=path):
                self.assertIsNotNone(finders.find(path), f"{path} が見つからない")

    def test_all_mapped_files_are_og_size(self):
        """全マップ値が OGP 標準の 1200×630"""
        for path in self._mapped_paths():
            with self.subTest(path=path):
                located = finders.find(path)
                with Image.open(located) as image:
                    self.assertEqual(image.size, (IMAGE_WIDTH, IMAGE_HEIGHT))
