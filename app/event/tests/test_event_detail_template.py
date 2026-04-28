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
        self.assertIn("aspect-ratio: 16 / 9;", template)
        self.assertIn("event-detail-thumbnail", template)

    def test_detail_form_expands_optional_fields_when_errors_exist(self):
        """折りたたみ対象フィールドにエラーがある場合は詳細設定を開く."""
        template = (
            Path(__file__).resolve().parents[1] / "templates" / "event" / "detail_form.html"
        ).read_text(encoding="utf-8")

        self.assertIn("function hasOptionalFieldErrors(config)", template)
        self.assertIn("hasOptionalFieldErrors(config)", template)

    def test_detail_form_guides_slide_upload_before_url_input(self):
        """スライドPDFアップロードを基本操作として案内する."""
        template = (
            Path(__file__).resolve().parents[1] / "templates" / "event" / "detail_form.html"
        ).read_text(encoding="utf-8")

        self.assertIn("最初にスライドPDFをアップロード", template)
        self.assertIn("URL入力のみでは記事は生成されません", template)
        self.assertIn(
            "const optionalFields = ['slide_file', 'slide_url', 'thumbnail_image'",
            template,
        )
        self.assertIn(
            "show: ['theme', 'speaker', 'start_time', 'duration', 'slide_file', 'slide_url'",
            template,
        )
