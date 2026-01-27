import os
from io import BytesIO
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from ta_hub.libs import resize_and_convert_image
from ta_hub.models import ImageFile


class ImageFileTestCase(TestCase):
    def setUp(self):
        # テストデータのパスを設定
        self.test_data_dir = os.path.join(os.path.dirname(__file__), 'input_data')

    def test_resize_jpg(self):
        # JPG画像をアップロードしてリサイズされることを確認
        with open(os.path.join(self.test_data_dir, 'sample.jpg'), 'rb') as f:
            uploaded_file = SimpleUploadedFile("sample.jpg", f.read(), content_type="image/jpeg")
            image_file = ImageFile.objects.create(image=uploaded_file)

        # リサイズ後の画像サイズを確認
        with image_file.image.open() as img_file:
            img = Image.open(img_file)
            width, height = img.size
            self.assertLessEqual(width, 720)
            self.assertLessEqual(height, 720)

        # ファイル名の拡張子が.pngに変更されていることを確認
        self.assertTrue(image_file.image.name.endswith('.png'))

    def test_resize_png(self):
        # PNG画像をアップロードしてリサイズされることを確認
        with open(os.path.join(self.test_data_dir, 'sample.png'), 'rb') as f:
            uploaded_file = SimpleUploadedFile("sample.png", f.read(), content_type="image/png")
            image_file = ImageFile.objects.create(image=uploaded_file)

        # リサイズ後の画像サイズを確認
        with image_file.image.open() as img_file:
            img = Image.open(img_file)
            width, height = img.size
            self.assertLessEqual(width, 720)
            self.assertLessEqual(height, 720)

        # ファイル名の拡張子が.pngに変更されていることを確認
        self.assertTrue(image_file.image.name.endswith('.png'))


class ResizeAndConvertImageTestCase(TestCase):
    """resize_and_convert_image関数の単体テスト"""

    def _create_test_image(self, width=100, height=100):
        """テスト用の画像を作成"""
        img = Image.new('RGB', (width, height), color='red')
        buffer = BytesIO()
        img.save(buffer, format='JPEG')
        buffer.seek(0)
        return buffer

    def test_filename_does_not_nest_directory(self):
        """ファイル名にディレクトリパスが含まれている場合、多重ネストしないことを確認"""
        # モック画像フィールドを作成
        mock_image_field = MagicMock()
        mock_image_field.name = 'poster/image.jpg'
        mock_image_field.file = self._create_test_image()
        mock_image_field.__bool__ = lambda self: True

        # save メソッドの呼び出しを記録
        saved_filenames = []

        def capture_save(filename, *args, **kwargs):
            saved_filenames.append(filename)

        mock_image_field.save = capture_save

        # 関数を実行
        resize_and_convert_image(mock_image_field, max_size=100, output_format='JPEG')

        # ファイル名にディレクトリパスが含まれていないことを確認
        self.assertEqual(len(saved_filenames), 1)
        saved_filename = saved_filenames[0]
        # poster/poster/image-100.jpeg のような多重ネストが発生していないことを確認
        self.assertFalse(saved_filename.startswith('poster/'))
        self.assertEqual(saved_filename, 'image-100.jpeg')

    def test_filename_with_simple_name(self):
        """シンプルなファイル名の場合も正しく処理されることを確認"""
        mock_image_field = MagicMock()
        mock_image_field.name = 'simple.png'
        mock_image_field.file = self._create_test_image()
        mock_image_field.__bool__ = lambda self: True

        saved_filenames = []

        def capture_save(filename, *args, **kwargs):
            saved_filenames.append(filename)

        mock_image_field.save = capture_save

        resize_and_convert_image(mock_image_field, max_size=200, output_format='PNG')

        self.assertEqual(len(saved_filenames), 1)
        self.assertEqual(saved_filenames[0], 'simple-200.png')

    def test_filename_with_nested_directory(self):
        """深くネストされたディレクトリパスでも正しく処理されることを確認"""
        mock_image_field = MagicMock()
        mock_image_field.name = 'uploads/2024/01/poster/image.jpg'
        mock_image_field.file = self._create_test_image()
        mock_image_field.__bool__ = lambda self: True

        saved_filenames = []

        def capture_save(filename, *args, **kwargs):
            saved_filenames.append(filename)

        mock_image_field.save = capture_save

        resize_and_convert_image(mock_image_field, max_size=500, output_format='JPEG')

        self.assertEqual(len(saved_filenames), 1)
        # ディレクトリ部分が除去され、ファイル名のみになることを確認
        self.assertEqual(saved_filenames[0], 'image-500.jpeg')
