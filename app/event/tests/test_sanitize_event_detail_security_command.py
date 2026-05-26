from unittest.mock import MagicMock

from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase

from community.models import Community
from event.management.commands.sanitize_event_detail_security import (
    _is_pdf_magic_bytes,
    _is_self_domain_url,
    sanitize_event_detail_contents,
)
from event.models import Event, EventDetail


class TestSanitizeEventDetailSecurityCommand(TestCase):
    def test_pdf_magic_bytes_logs_file_errors(self):
        """slide_fileの読み取りとclose失敗はログ出力してFalseを返す"""
        slide_file = MagicMock()
        slide_file.name = "broken.pdf"
        slide_file.open.side_effect = OSError("open failed")
        slide_file.close.side_effect = OSError("close failed")

        with self.assertLogs(
            "event.management.commands.sanitize_event_detail_security",
            level="ERROR",
        ) as log_ctx:
            result = _is_pdf_magic_bytes(slide_file)

        self.assertFalse(result)
        self.assertIn("slide_fileのPDFマジックバイト判定に失敗しました", log_ctx.output[0])
        self.assertIn("slide_fileのクローズに失敗しました", log_ctx.output[1])

    def test_detach_invalid_slide_file_and_sanitize_contents(self):
        community = Community.objects.create(name="テスト集会")
        event = Event.objects.create(date="2024-05-24", community=community)
        event_detail = EventDetail.objects.create(
            event=event,
            theme="テストテーマ",
            speaker="テストスピーカー",
        )

        event_detail.contents = (
            "before\n"
            '<iframe src="https://data.vrc-ta-hub.com/slide/xssexploit.pdf" width="500" height="500"></iframe>\n'
            "after\n"
        )
        event_detail.save(update_fields=["contents"])

        # 既に不正ファイルが保存されている前提を作る（FileField.saveはvalidatorを通らない）
        event_detail.slide_file.save(
            "evil.pdf",
            ContentFile(b"<html><script>alert(1)</script></html>"),
            save=True,
        )

        call_command(
            "sanitize_event_detail_security",
            ids=str(event_detail.id),
            apply=True,
            delete_files=True,
            verbosity=0,
        )

        event_detail.refresh_from_db()
        self.assertNotIn("<iframe", event_detail.contents)
        self.assertFalse(bool(event_detail.slide_file))


class TestIsSelfDomainUrl(TestCase):
    """偽装ドメインを suffix 一致で誤って自ドメイン扱いしないことを確認する."""

    def test_exact_self_domain_is_self(self):
        self.assertTrue(_is_self_domain_url("https://vrc-ta-hub.com/foo"))

    def test_subdomain_is_self(self):
        self.assertTrue(_is_self_domain_url("https://data.vrc-ta-hub.com/slide.pdf"))

    def test_spoofed_prefix_domain_is_not_self(self):
        # 旧実装の endswith("vrc-ta-hub.com") は True を返してしまうが、
        # is_site_domain ベースの判定では False になる。
        self.assertFalse(_is_self_domain_url("https://evilvrc-ta-hub.com/exploit"))

    def test_spoofed_domain_with_self_in_path_is_not_self(self):
        self.assertFalse(
            _is_self_domain_url("https://attacker.example.com/vrc-ta-hub.com"),
        )

    def test_different_domain_is_not_self(self):
        self.assertFalse(_is_self_domain_url("https://example.com/page"))

    def test_url_with_port_is_handled(self):
        # hostname プロパティを使うので、ポート番号付きでも正しく判定できる。
        self.assertTrue(_is_self_domain_url("https://vrc-ta-hub.com:8443/path"))
        self.assertFalse(_is_self_domain_url("https://evilvrc-ta-hub.com:8443/path"))


class TestSanitizeEventDetailContentsSpoofedIframe(TestCase):
    """偽装ドメインの iframe を「自ドメイン」と誤判定しないことを確認する.

    本コマンドの責務は「自ドメイン配下の iframe を除去する」ことに限定される
    （外部 iframe は description にあるとおり対象外）。
    したがって evilvrc-ta-hub.com のような偽装ドメインは "外部扱い" となり、
    DB 上には残る。最終的な XSS 防止は `convert_markdown()` 側の allowlist が担う。
    本テストは「偽装ドメインが自ドメインとして除去ロジックを発動させない」
    （= is_site_domain 修正の回帰防止）ことだけを保証する。
    """

    def test_spoofed_iframe_is_not_classified_as_self_domain(self):
        contents = (
            "before\n"
            '<iframe src="https://evilvrc-ta-hub.com/exploit" '
            'width="500" height="500"></iframe>\n'
            "after\n"
        )
        sanitized, notes = sanitize_event_detail_contents(contents)

        # 自ドメインとして除去されていないことを確認
        self.assertNotIn("Removed self-domain <iframe>", notes)
        # iframe 本体が外部扱いとして温存されていることを直接確認
        self.assertIn("<iframe", sanitized)
        self.assertIn("evilvrc-ta-hub.com", sanitized)

    def test_self_domain_iframe_is_removed(self):
        contents = (
            '<iframe src="https://data.vrc-ta-hub.com/slide.pdf"></iframe>'
        )
        sanitized, notes = sanitize_event_detail_contents(contents)

        self.assertNotIn("<iframe", sanitized)
        self.assertIn("Removed self-domain <iframe>", notes)
