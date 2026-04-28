from pathlib import Path

from django.test import SimpleTestCase


class EventDetailTemplateTest(SimpleTestCase):
    """event/detail.html の回帰テスト."""

    def test_generate_button_script_is_null_safe(self):
        """記事生成ボタンがない公開ページでも JS が落ちない."""
        template = (
            Path(__file__).resolve().parents[1] / "templates" / "event" / "detail.html"
        ).read_text(encoding="utf-8")

        self.assertIn("const generateButton = document.getElementById('generate-button');", template)
        self.assertIn("if (generateButton) {", template)
        self.assertNotIn("document.getElementById('generate-button').addEventListener", template)

    def test_thumbnail_image_is_used_on_detail_page(self):
        """サムネイル画像がOGPと本文上部に表示される."""
        template = (
            Path(__file__).resolve().parents[1] / "templates" / "event" / "detail.html"
        ).read_text(encoding="utf-8")

        self.assertIn("{% if event_detail.thumbnail_image %}", template)
        self.assertIn('content="{{ event_detail.thumbnail_image.url }}"', template)
        self.assertIn("event_detail.thumbnail_image.url|cf_resize:'1200'", template)
