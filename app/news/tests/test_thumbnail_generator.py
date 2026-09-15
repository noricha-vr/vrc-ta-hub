"""news.thumbnail_generator（Pillow 描画）のテスト"""
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase
from PIL import Image

from news import thumbnail_generator as generator

WRAP_WIDTH = generator.CONTENT_WIDTH
SAMPLE_ACCENT = "#1B7DE6"


class WrapTextTestCase(SimpleTestCase):
    """wrap_text の折り返しと禁則処理"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.font = generator.load_font(generator.TITLE_MAX_FONT_SIZE)

    def test_short_text_is_single_line(self):
        """幅に収まる文字列は 1 行のまま"""
        self.assertEqual(generator.wrap_text("短いタイトル", self.font, WRAP_WIDTH), ["短いタイトル"])

    def test_long_text_is_wrapped_within_width(self):
        """長い文字列は指定幅以内に折り返される"""
        lines = generator.wrap_text("あ" * 60, self.font, WRAP_WIDTH)

        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertLessEqual(self.font.getlength(line), WRAP_WIDTH)

    def test_no_line_starts_with_prohibited_character(self):
        """行頭禁則文字が行頭に来ない"""
        text = "あ" * 19 + "、" + "い" * 40
        lines = generator.wrap_text(text, self.font, WRAP_WIDTH)

        self.assertGreater(len(lines), 1)
        for line in lines[1:]:
            self.assertNotIn(line[0], generator.LEADING_PROHIBITED)

    def test_no_line_ends_with_prohibited_character(self):
        """行末禁則文字が行末に来ない"""
        text = "あ" * 19 + "【重要】" + "い" * 40
        lines = generator.wrap_text(text, self.font, WRAP_WIDTH)

        for line in lines:
            self.assertNotIn(line[-1], generator.TRAILING_PROHIBITED)

    def test_ascii_word_is_not_split(self):
        """ASCII の連続（英単語）を途中で割らない"""
        text = "あ" * 18 + "Announcement" + "い" * 10
        lines = generator.wrap_text(text, self.font, WRAP_WIDTH)

        self.assertTrue(any("Announcement" in line for line in lines))

    def test_leading_space_is_dropped(self):
        """折り返し後の行頭スペースを落とす"""
        text = "あ" * 19 + " " + "い" * 20
        lines = generator.wrap_text(text, self.font, WRAP_WIDTH)

        for line in lines:
            self.assertEqual(line, line.strip())


class FitTitleTestCase(SimpleTestCase):
    """fit_title のフォント縮小と行数制限"""

    def test_short_title_uses_max_font_size(self):
        """短いタイトルは最大サイズのまま"""
        _, font = generator.fit_title("短い")
        self.assertEqual(font.size, generator.TITLE_MAX_FONT_SIZE)

    def test_long_title_shrinks_font(self):
        """長いタイトルはフォントを縮小する"""
        long_title = (
            "VRC技術・学術系イベントHUB × Vketステージ コラボ【Vket技術学術WEEK】開催決定！"
        )
        lines, font = generator.fit_title(long_title)

        self.assertLess(font.size, generator.TITLE_MAX_FONT_SIZE)
        self.assertLessEqual(len(lines), generator.MAX_TITLE_LINES)

    def test_lines_fit_within_content_width(self):
        """各行が本文幅に収まる"""
        lines, font = generator.fit_title("あ" * 80)

        for line in lines:
            self.assertLessEqual(font.getlength(line), generator.CONTENT_WIDTH)

    def test_excess_text_is_truncated_with_ellipsis(self):
        """最小サイズでも入らない文字列は … で打ち切る"""
        lines, font = generator.fit_title("あ" * 400)

        self.assertEqual(len(lines), generator.MAX_TITLE_LINES)
        self.assertTrue(lines[-1].endswith(generator.ELLIPSIS))
        self.assertEqual(font.size, generator.TITLE_MIN_FONT_SIZE)

    def test_lines_fit_within_title_band(self):
        """行送り × 行数がタイトル領域の高さに収まる"""
        lines, font = generator.fit_title("あ" * 40)
        total_height = generator.line_height_for(font.size) * len(lines)

        self.assertLessEqual(total_height, generator.TITLE_BOTTOM - generator.TITLE_TOP)


class RenderThumbnailTestCase(SimpleTestCase):
    """render_thumbnail / save_thumbnail のテスト"""

    def test_render_returns_og_size_image(self):
        """1200×630 の RGB 画像を返す"""
        image = generator.render_thumbnail(
            title="テストタイトル", accent=SAMPLE_ACCENT, chip_label="アップデート"
        )

        self.assertEqual(image.size, (generator.IMAGE_WIDTH, generator.IMAGE_HEIGHT))
        self.assertEqual(image.mode, "RGB")

    def test_render_without_chip(self):
        """チップ無し（カテゴリ既定画像）でも描画できる"""
        image = generator.render_thumbnail(
            title="アップデート",
            accent=SAMPLE_ACCENT,
            max_font_size=generator.CATEGORY_TITLE_MAX_FONT_SIZE,
        )

        self.assertEqual(image.size, (generator.IMAGE_WIDTH, generator.IMAGE_HEIGHT))

    def test_render_is_deterministic(self):
        """同じ入力なら同じバイト列になる"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = []
            for index in range(2):
                image = generator.render_thumbnail(title="同一入力", accent=SAMPLE_ACCENT)
                paths.append(
                    generator.save_thumbnail(image, Path(tmp_dir) / f"{index}.png")
                )

            self.assertEqual(paths[0].read_bytes(), paths[1].read_bytes())

    def test_save_creates_png_file(self):
        """保存先ディレクトリを作って PNG を書き出す"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            destination = Path(tmp_dir) / "nested" / "thumb.png"
            image = generator.render_thumbnail(title="保存テスト", accent=SAMPLE_ACCENT)

            generator.save_thumbnail(image, destination)

            self.assertTrue(destination.exists())
            with Image.open(destination) as saved:
                self.assertEqual(saved.format, "PNG")
                self.assertEqual(
                    saved.size, (generator.IMAGE_WIDTH, generator.IMAGE_HEIGHT)
                )

    def test_missing_font_raises_clear_error(self):
        """フォント欠落時は原因の分かる例外を投げる"""
        with patch.object(generator, "FONT_PATH", Path("/nonexistent/NotoSansJP.otf")):
            with self.assertRaises(generator.ThumbnailAssetError) as ctx:
                generator.font_path()

        self.assertIn("フォントが見つかりません", str(ctx.exception))

    def test_missing_logo_raises_clear_error(self):
        """ロゴ欠落時も原因の分かる例外を投げる"""
        with patch.object(generator, "LOGO_PATH", Path("/nonexistent/logo.png")):
            with self.assertRaises(generator.ThumbnailAssetError):
                generator.logo_path()
