from django.test import TestCase
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from event.forms import EventDetailForm
from event.models import validate_pdf_file


class PDFValidationTest(TestCase):
    """PDFファイルバリデーションのテスト"""

    def test_validate_pdf_file_with_pdf(self):
        """PDFファイルは受け付ける"""
        # 有効なPDFマジックバイトを持つコンテンツ
        pdf_content = b'%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF'
        file = SimpleUploadedFile("test.pdf", pdf_content, content_type="application/pdf")
        # バリデーションエラーが発生しないことを確認
        try:
            validate_pdf_file(file)
        except ValidationError:
            self.fail("PDFファイルでValidationErrorが発生しました")
    
    def test_validate_pdf_file_with_non_pdf(self):
        """PDF以外のファイルは拒否する"""
        file = SimpleUploadedFile("test.jpg", b"JPEG content", content_type="image/jpeg")
        with self.assertRaises(ValidationError) as cm:
            validate_pdf_file(file)
        self.assertEqual(str(cm.exception.message), 'PDFファイルのみアップロード可能です。')
    
    def test_form_clean_slide_file_with_pdf(self):
        """フォームでPDFファイルを受け付ける"""
        # 有効なPDFマジックバイトを持つコンテンツ
        pdf_content = b'%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF'
        file = SimpleUploadedFile("test.pdf", pdf_content, content_type="application/pdf")
        form_data = {
            'theme': 'テストテーマ',
            'speaker': 'テストスピーカー',
            'start_time': '22:00',
            'duration': 30,
        }
        form = EventDetailForm(data=form_data, files={'slide_file': file})
        # clean_slide_fileメソッドを直接呼び出してテスト
        form.cleaned_data = {'slide_file': file}
        cleaned_file = form.clean_slide_file()
        self.assertEqual(cleaned_file, file)
    
    def test_form_clean_slide_file_with_non_pdf(self):
        """フォームでPDF以外のファイルを拒否する"""
        file = SimpleUploadedFile("test.docx", b"DOCX content", content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        form_data = {
            'theme': 'テストテーマ',
            'speaker': 'テストスピーカー',
            'start_time': '22:00',
            'duration': 30,
        }
        form = EventDetailForm(data=form_data, files={'slide_file': file})
        # clean_slide_fileメソッドを直接呼び出してテスト
        form.cleaned_data = {'slide_file': file}
        with self.assertRaises(ValidationError) as cm:
            form.clean_slide_file()
        self.assertEqual(str(cm.exception.message), 'PDFファイルのみアップロード可能です。')
    
    def test_form_clean_slide_file_size_limit(self):
        """30MBを超えるファイルを拒否する"""
        # PDFヘッダー + 31MBのパディングを持つファイルを作成
        pdf_header = b'%PDF-1.4\n'
        large_content = pdf_header + b"X" * (31 * 1024 * 1024)
        file = SimpleUploadedFile("large.pdf", large_content, content_type="application/pdf")
        form_data = {
            'theme': 'テストテーマ',
            'speaker': 'テストスピーカー',
            'start_time': '22:00',
            'duration': 30,
        }
        form = EventDetailForm(data=form_data, files={'slide_file': file})
        # clean_slide_fileメソッドを直接呼び出してテスト
        form.cleaned_data = {'slide_file': file}
        with self.assertRaises(ValidationError) as cm:
            form.clean_slide_file()
        self.assertEqual(str(cm.exception.message), 'ファイルサイズが30MBを超えています。')


class PDFMagicByteValidationTest(TestCase):
    """PDFマジックバイト検証のテスト（XSS対策）"""

    def test_html_file_with_pdf_extension_rejected(self):
        """HTML内容を持つ.pdfファイルは拒否される"""
        html_content = b'<html><script>alert(1)</script></html>'
        fake_pdf = SimpleUploadedFile('malicious.pdf', html_content, content_type='text/html')
        with self.assertRaises(ValidationError) as context:
            validate_pdf_file(fake_pdf)
        self.assertIn('PDFファイルのみアップロード可能です', str(context.exception))

    def test_javascript_file_with_pdf_extension_rejected(self):
        """JavaScript内容を持つ.pdfファイルは拒否される"""
        js_content = b'alert(document.cookie)'
        fake_pdf = SimpleUploadedFile('script.pdf', js_content, content_type='application/javascript')
        with self.assertRaises(ValidationError) as context:
            validate_pdf_file(fake_pdf)
        self.assertIn('PDFファイルのみアップロード可能です', str(context.exception))

    def test_svg_file_with_pdf_extension_rejected(self):
        """SVG（XMLベース）内容を持つ.pdfファイルは拒否される"""
        svg_content = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        fake_pdf = SimpleUploadedFile('image.pdf', svg_content, content_type='image/svg+xml')
        with self.assertRaises(ValidationError) as context:
            validate_pdf_file(fake_pdf)
        self.assertIn('PDFファイルのみアップロード可能です', str(context.exception))

    def test_valid_pdf_magic_bytes_accepted(self):
        """正しいマジックバイトを持つPDFは許可される"""
        # 最小限の有効なPDFファイル構造
        pdf_content = b'%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF'
        valid_pdf = SimpleUploadedFile('valid.pdf', pdf_content, content_type='application/pdf')
        # ValidationErrorが発生しないことを確認
        try:
            validate_pdf_file(valid_pdf)
        except ValidationError:
            self.fail("有効なPDFファイルでValidationErrorが発生しました")

    def test_empty_file_rejected(self):
        """空のファイルは拒否される"""
        empty_file = SimpleUploadedFile('empty.pdf', b'', content_type='application/pdf')
        with self.assertRaises(ValidationError) as context:
            validate_pdf_file(empty_file)
        self.assertIn('PDFファイルのみアップロード可能です', str(context.exception))

    def test_random_binary_with_pdf_extension_rejected(self):
        """ランダムなバイナリ内容を持つ.pdfファイルは拒否される"""
        random_content = b'\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09'
        fake_pdf = SimpleUploadedFile('random.pdf', random_content, content_type='application/octet-stream')
        with self.assertRaises(ValidationError) as context:
            validate_pdf_file(fake_pdf)
        self.assertIn('PDFファイルのみアップロード可能です', str(context.exception))

    def test_file_pointer_reset_after_validation(self):
        """バリデーション後にファイルポインタがリセットされている"""
        pdf_content = b'%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF'
        valid_pdf = SimpleUploadedFile('valid.pdf', pdf_content, content_type='application/pdf')
        validate_pdf_file(valid_pdf)
        # ファイルポインタが先頭にリセットされていることを確認
        self.assertEqual(valid_pdf.read(5), b'%PDF-')
