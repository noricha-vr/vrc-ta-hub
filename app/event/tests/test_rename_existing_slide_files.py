from django.test import SimpleTestCase

from event.management.commands.rename_existing_slide_files import (
    _UUID_PDF_RE,
    _build_url_variants,
)


class UuidPdfRegexTest(SimpleTestCase):
    def test_matches_uuid_path(self):
        self.assertRegex("slide/" + "a" * 32 + ".pdf", _UUID_PDF_RE)

    def test_rejects_old_format(self):
        self.assertNotRegex("slide/発表資料.pdf", _UUID_PDF_RE)

    def test_rejects_uppercase_hex(self):
        self.assertNotRegex("slide/" + "A" * 32 + ".pdf", _UUID_PDF_RE)

    def test_rejects_missing_prefix(self):
        self.assertNotRegex("a" * 32 + ".pdf", _UUID_PDF_RE)


class BuildUrlVariantsTest(SimpleTestCase):
    HOST = "data.vrc-ta-hub.com"

    def test_ascii_name_returns_single_variant(self):
        pairs = _build_url_variants(self.HOST, "slide/foo.pdf", "slide/new.pdf")
        self.assertEqual(
            pairs,
            [(f"https://{self.HOST}/slide/foo.pdf", f"https://{self.HOST}/slide/new.pdf")],
        )

    def test_japanese_name_returns_raw_and_encoded(self):
        old = "slide/発表資料.pdf"
        new = "slide/abc.pdf"
        pairs = _build_url_variants(self.HOST, old, new)
        self.assertEqual(len(pairs), 2)
        raw_old, encoded_old = pairs[0][0], pairs[1][0]
        self.assertEqual(raw_old, f"https://{self.HOST}/{old}")
        self.assertEqual(
            encoded_old,
            f"https://{self.HOST}/slide/%E7%99%BA%E8%A1%A8%E8%B3%87%E6%96%99.pdf",
        )
        self.assertEqual(pairs[0][1], pairs[1][1])

    def test_slash_in_path_preserved(self):
        pairs = _build_url_variants(self.HOST, "slide/sub/dir/日本語.pdf", "slide/x.pdf")
        for old_url, _ in pairs:
            self.assertIn("/slide/sub/dir/", old_url)
