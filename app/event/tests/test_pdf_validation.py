from django.test import TestCase
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from event.forms import EventDetailForm
from event.models import validate_pdf_file


class PDFValidationTest(TestCase):
    """PDFファイルバリデーションのテスト"""
    
    def test_validate_pdf_file_with_pdf(self):
        """PDFファイルは受け付ける"""
        file = SimpleUploadedFile("test.pdf", b"PDF content", content_type="application/pdf")
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
        file = SimpleUploadedFile("test.pdf", b"PDF content", content_type="application/pdf")
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
        # 31MBのファイルを作成
        large_content = b"X" * (31 * 1024 * 1024)
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