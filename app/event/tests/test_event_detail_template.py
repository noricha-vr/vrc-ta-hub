import re
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

    def test_admin_ui_keeps_order_permissions_and_speaker_layout(self):
        """管理UIの順序・権限境界・発表者アカウントの表示を保つ."""
        template = self._detail_template()

        manage_start = template.index("{% if can_manage_event_detail %}")
        article_notes = template.index("希望の記事が作成されない場合")
        speaker_account = template.index('class="speaker-account-card')
        analytics_condition = template.index("{% if can_view_analytics %}")
        analytics_section = template.index("analytics/_chart_section.html")

        self.assertLess(manage_start, article_notes)
        self.assertLess(article_notes, speaker_account)
        self.assertLess(speaker_account, analytics_section)
        self.assertLess(analytics_condition, analytics_section)
        self.assertRegex(
            template,
            r"{% endif %}\s*\n\s*{# アクセス解析は操作群.*? #}\s*\n\s*{% if can_view_analytics %}",
        )
        applicant_branch = template.split("{% if event_detail.applicant %}", 1)[1].split(
            "{% else %}", 1
        )[0]
        unlinked_branch = template.split("{% if event_detail.applicant %}", 1)[1].split(
            "{% else %}", 1
        )[1].split("{% endif %}", 1)[0]

        self.assertRegex(
            applicant_branch,
            re.compile(
                r'class="speaker-account-linked d-flex flex-wrap align-items-center gap-2".*?'
                r'id="speaker-account-heading" class="h5 mb-0".*?'
                r'class="mb-0".*?data-bs-target="#speaker-unlink-modal"',
                re.DOTALL,
            ),
        )
        self.assertIn('class="text-muted mb-3"', unlinked_branch)
        self.assertIn('id="speaker-invite-form"', unlinked_branch)
        self.assertIn('action="{% url \'event:speaker_invite_issue\' event_detail.pk %}"', unlinked_branch)
        self.assertIn('id="speaker-invite-url" class="form-control" readonly', unlinked_branch)

    def _detail_template(self):
        return (
            Path(__file__).resolve().parents[1] / "templates" / "event" / "detail.html"
        ).read_text(encoding="utf-8")

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

    def test_article_submit_feedback_prevents_double_submit(self):
        """保存ボタン連打による二重送信をフォーム側で止める."""
        template = (
            Path(__file__).resolve().parents[1] / "templates" / "event" / "includes"
            / "article_generation_submit_feedback.html"
        ).read_text(encoding="utf-8")

        self.assertIn("let isSubmitting = false;", template)
        self.assertIn("if (isSubmitting) {", template)
        self.assertIn("event.preventDefault();", template)
        self.assertIn("isSubmitting = true;", template)
        self.assertIn("submitButton.disabled = true;", template)

    def test_article_submit_feedback_shows_saving_state_without_generation(self):
        """記事生成しない通常保存でも保存中表示に切り替える."""
        template = (
            Path(__file__).resolve().parents[1] / "templates" / "event" / "includes"
            / "article_generation_submit_feedback.html"
        ).read_text(encoding="utf-8")

        self.assertIn("function activateSavingState()", template)
        self.assertIn("submitButton.dataset.submitState = 'saving';", template)
        self.assertIn("const loadingLabelDefaultHtml = loadingLabel?.innerHTML || '';", template)
        self.assertIn("loadingLabel.innerHTML = '保存中…';", template)
        self.assertIn("window.addEventListener('pageshow', resetSubmitState);", template)
