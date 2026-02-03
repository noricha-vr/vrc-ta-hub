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

        # ファイル名の拡張子が.jpegに変更されていることを確認（モデルはJPEGフォーマットで保存）
        self.assertTrue(image_file.image.name.endswith('.jpeg'))

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

        # ファイル名の拡張子が.jpegに変更されていることを確認（モデルはJPEGフォーマットで保存）
        self.assertTrue(image_file.image.name.endswith('.jpeg'))


class ResizeAndConvertImageTestCase(TestCase):
    """resize_and_convert_image関数の単体テスト"""

    def _create_test_image(self, width=100, height=100):
        """テスト用の画像を作成"""
        img = Image.new('RGB', (width, height), color='red')
        buffer = BytesIO()
        img.save(buffer, format='JPEG')
        buffer.seek(0)
        return buffer

    def _create_mock_storage(self, saved_paths):
        """テスト用のモックストレージを作成"""
        mock_storage = MagicMock()
        mock_storage.exists.return_value = True

        def capture_save(path, content):
            saved_paths.append(path)
            return path  # 保存されたパスをそのまま返す

        mock_storage.save = capture_save
        return mock_storage

    def test_filename_does_not_nest_directory(self):
        """ファイル名にディレクトリパスが含まれている場合、多重ネストしないことを確認"""
        # モック画像フィールドを作成
        mock_image_field = MagicMock()
        mock_image_field.name = 'poster/image.jpg'
        mock_image_field.file = self._create_test_image()
        mock_image_field.__bool__ = lambda self: True

        # ストレージに保存されるパスを記録
        saved_paths = []
        mock_image_field.storage = self._create_mock_storage(saved_paths)

        # 関数を実行
        resize_and_convert_image(mock_image_field, max_size=100, output_format='JPEG')

        # 保存されたパスを確認
        self.assertEqual(len(saved_paths), 1)
        saved_path = saved_paths[0]
        # poster/poster/image-100.jpeg のような多重ネストが発生していないことを確認
        self.assertEqual(saved_path, 'poster/image-100.jpeg')
        # image_field.name が更新されていることを確認
        self.assertEqual(mock_image_field.name, 'poster/image-100.jpeg')

    def test_filename_with_simple_name(self):
        """シンプルなファイル名の場合（新規作成時）、image_field.save()が呼ばれることを確認"""
        mock_image_field = MagicMock()
        mock_image_field.name = 'simple.png'  # ディレクトリパスなし = 新規作成
        mock_image_field.file = self._create_test_image()
        mock_image_field.__bool__ = lambda self: True

        # save メソッドの呼び出しを記録
        saved_filenames = []

        def capture_save(filename, *args, **kwargs):
            saved_filenames.append(filename)

        mock_image_field.save = capture_save

        resize_and_convert_image(mock_image_field, max_size=200, output_format='PNG')

        # 新規作成時は image_field.save() が呼ばれる
        self.assertEqual(len(saved_filenames), 1)
        self.assertEqual(saved_filenames[0], 'simple-200.png')

    def test_filename_with_nested_directory(self):
        """深くネストされたディレクトリパスでも正しく処理されることを確認"""
        mock_image_field = MagicMock()
        mock_image_field.name = 'uploads/2024/01/poster/image.jpg'
        mock_image_field.file = self._create_test_image()
        mock_image_field.__bool__ = lambda self: True

        saved_paths = []
        mock_image_field.storage = self._create_mock_storage(saved_paths)

        resize_and_convert_image(mock_image_field, max_size=500, output_format='JPEG')

        self.assertEqual(len(saved_paths), 1)
        # ディレクトリ構造が維持されていることを確認
        self.assertEqual(saved_paths[0], 'uploads/2024/01/poster/image-500.jpeg')
        self.assertEqual(mock_image_field.name, 'uploads/2024/01/poster/image-500.jpeg')

    def test_multiple_updates_do_not_nest_path(self):
        """複数回更新してもパスが多重ネストしないことを確認"""
        mock_image_field = MagicMock()
        mock_image_field.name = 'poster/image.jpg'
        mock_image_field.file = self._create_test_image()
        mock_image_field.__bool__ = lambda self: True

        saved_paths = []
        mock_storage = self._create_mock_storage(saved_paths)
        mock_image_field.storage = mock_storage

        # 1回目の更新
        resize_and_convert_image(mock_image_field, max_size=100, output_format='JPEG')
        self.assertEqual(mock_image_field.name, 'poster/image-100.jpeg')

        # 2回目の更新をシミュレート（新しいファイルがアップロードされた想定）
        mock_image_field.file = self._create_test_image()
        # nameは変わらない想定（同じパスに再保存）
        resize_and_convert_image(mock_image_field, max_size=100, output_format='JPEG')

        # 2回とも同じディレクトリ構造であることを確認
        self.assertEqual(len(saved_paths), 2)
        self.assertEqual(saved_paths[0], 'poster/image-100.jpeg')
        self.assertEqual(saved_paths[1], 'poster/image-100-100.jpeg')
        # poster/poster/... のような多重ネストは発生しない
        for path in saved_paths:
            self.assertNotIn('poster/poster/', path)

    def test_file_not_found_error_is_caught(self):
        """ストレージ上にファイルが存在しない場合、FileNotFoundErrorをキャッチしてスキップすることを確認"""
        mock_image_field = MagicMock()
        mock_image_field.name = 'poster/missing.jpg'
        # ファイルを開くとFileNotFoundErrorが発生
        mock_image_field.file = MagicMock()
        mock_image_field.__bool__ = lambda self: True

        # Image.openでFileNotFoundErrorを発生させるようにモック
        with patch('ta_hub.libs.Image.open', side_effect=FileNotFoundError('File not found')):
            # エラーが発生せずに正常終了することを確認
            result = resize_and_convert_image(mock_image_field, max_size=100, output_format='JPEG')
            self.assertIsNone(result)

    def test_other_exception_is_caught(self):
        """その他の例外（壊れたファイル等）もキャッチしてスキップすることを確認"""
        mock_image_field = MagicMock()
        mock_image_field.name = 'poster/corrupted.jpg'
        mock_image_field.file = MagicMock()
        mock_image_field.__bool__ = lambda self: True

        # Image.openで例外を発生させる
        with patch('ta_hub.libs.Image.open', side_effect=Exception('Corrupted file')):
            # エラーが発生せずに正常終了することを確認
            result = resize_and_convert_image(mock_image_field, max_size=100, output_format='JPEG')
            self.assertIsNone(result)

    def test_none_image_field_returns_early(self):
        """image_fieldがNoneの場合、早期リターンすることを確認"""
        # エラーが発生せずに正常終了することを確認
        result = resize_and_convert_image(None, max_size=100, output_format='JPEG')
        self.assertIsNone(result)

    def test_falsy_image_field_returns_early(self):
        """image_fieldがFalsyな場合、早期リターンすることを確認"""
        mock_image_field = MagicMock()
        mock_image_field.__bool__ = lambda self: False

        result = resize_and_convert_image(mock_image_field, max_size=100, output_format='JPEG')
        self.assertIsNone(result)
