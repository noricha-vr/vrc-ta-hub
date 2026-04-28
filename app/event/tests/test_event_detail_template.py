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

    def test_thumbnail_is_rendered_above_detail_info(self):
        """サムネイル表示が詳細情報 include より前にある."""
        template = (
            Path(__file__).resolve().parents[1] / "templates" / "event" / "detail.html"
        ).read_text(encoding="utf-8")

        thumbnail_index = template.index("event-detail-thumbnail")
        detail_info_index = template.index("event/detail_info_lt.html")

        self.assertLess(thumbnail_index, detail_info_index)
        self.assertIn("{% if event_detail.thumbnail %}", template)
        self.assertIn('meta property="og:image" content="{{ event_detail.thumbnail.url }}"', template)
