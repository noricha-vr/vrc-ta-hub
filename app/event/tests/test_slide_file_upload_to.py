import re

from django.test import TestCase

from event.models import slide_file_upload_to


class SlideFileUploadToTest(TestCase):
    """slide_file の upload_to コールバックのテスト"""

    _UUID_PDF_RE = re.compile(r'^slide/[0-9a-f]{32}\.pdf$')

    def _assert_normalized(self, generated):
        self.assertRegex(generated, self._UUID_PDF_RE)

    def test_returns_uuid_path_for_pdf(self):
        """元のファイル名をUUIDに置き換え、slide/<uuid>.pdf を返す"""
        path = slide_file_upload_to(instance=None, filename='発表資料_最終版.pdf')
        self._assert_normalized(path)

    def test_two_uploads_produce_unique_paths(self):
        """同名でも別パスが生成される（衝突しない）"""
        path1 = slide_file_upload_to(None, 'slides.pdf')
        path2 = slide_file_upload_to(None, 'slides.pdf')
        self.assertNotEqual(path1, path2)

    def test_extension_forced_to_pdf(self):
        """拡張子が .pdf 以外でも .pdf に強制する（validatorで弾かれる前提だが防御的に）"""
        path = slide_file_upload_to(None, 'malicious.exe')
        self._assert_normalized(path)

    def test_uppercase_pdf_normalized(self):
        """大文字拡張子も小文字 .pdf に正規化される"""
        path = slide_file_upload_to(None, 'SLIDES.PDF')
        self._assert_normalized(path)

    def test_original_filename_not_leaked(self):
        """元ファイル名が結果パスに含まれない（情報漏洩防止）"""
        secret = '社外秘_クライアント名_提案資料'
        path = slide_file_upload_to(None, f'{secret}.pdf')
        self.assertNotIn(secret, path)
