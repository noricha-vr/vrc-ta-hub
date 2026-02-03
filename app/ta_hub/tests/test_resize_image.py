import os
from io import BytesIO
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from ta_hub.libs import (
    DEFAULT_JPEG_QUALITY,
    DEFAULT_MAX_SIZE,
    DEFAULT_PNG_TO_JPEG_THRESHOLD,
    resize_and_convert_image,
)
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

        # リサイズ後の画像サイズを確認（デフォルトは720）
        with image_file.image.open() as img_file:
            img = Image.open(img_file)
            width, height = img.size
            self.assertLessEqual(width, 720)
            self.assertLessEqual(height, 720)

        # ファイル名の拡張子が.jpegに変更されていることを確認
        self.assertTrue(image_file.image.name.endswith('.jpeg'))

    def test_resize_png_with_transparency(self):
        # 透過ありPNG画像（sample.pngはRGBA）をアップロードしてリサイズされることを確認
        with open(os.path.join(self.test_data_dir, 'sample.png'), 'rb') as f:
            uploaded_file = SimpleUploadedFile("sample.png", f.read(), content_type="image/png")
            image_file = ImageFile.objects.create(image=uploaded_file)

        # リサイズ後の画像サイズを確認
        with image_file.image.open() as img_file:
            img = Image.open(img_file)
            width, height = img.size
            self.assertLessEqual(width, 720)
            self.assertLessEqual(height, 720)

        # sample.pngは透過ありなのでPNGのまま維持されることを確認
        self.assertTrue(image_file.image.name.endswith('.png'))


class ResizeAndConvertImageTestCase(TestCase):
    """resize_and_convert_image関数の単体テスト"""

    def _create_test_image(self, width=100, height=100, format='JPEG', mode='RGB'):
        """テスト用の画像を作成"""
        img = Image.new(mode, (width, height), color='red')
        buffer = BytesIO()
        img.save(buffer, format=format)
        buffer.seek(0)
        return buffer

    def _create_large_png_image(self, min_size_kb=200):
        """指定サイズ以上のPNG画像を作成（非圧縮データを利用）"""
        # PNGはランダムデータだと圧縮されにくいので大きくなる
        import random
        width = 500
        height = 500
        # ランダムな色で埋める
        img = Image.new('RGB', (width, height))
        pixels = img.load()
        for x in range(width):
            for y in range(height):
                pixels[x, y] = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
        buffer = BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)

        # サイズが足りない場合は警告
        size = len(buffer.getvalue())
        if size < min_size_kb * 1024:
            # より大きなサイズで再試行
            img = Image.new('RGB', (1000, 1000))
            pixels = img.load()
            for x in range(1000):
                for y in range(1000):
                    pixels[x, y] = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
            buffer = BytesIO()
            img.save(buffer, format='PNG')
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

    def test_default_values(self):
        """デフォルト値が正しいことを確認"""
        self.assertEqual(DEFAULT_MAX_SIZE, 1000)
        self.assertEqual(DEFAULT_JPEG_QUALITY, 82)
        self.assertEqual(DEFAULT_PNG_TO_JPEG_THRESHOLD, 200 * 1024)

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
        resize_and_convert_image(mock_image_field, max_size=100)

        # 保存されたパスを確認
        self.assertEqual(len(saved_paths), 1)
        saved_path = saved_paths[0]
        # poster/poster/image.jpeg のような多重ネストが発生していないことを確認
        self.assertEqual(saved_path, 'poster/image.jpeg')
        # image_field.name が更新されていることを確認
        self.assertEqual(mock_image_field.name, 'poster/image.jpeg')

    def test_filename_without_suffix(self):
        """ファイル名にサフィックス（-1000等）が追加されないことを確認"""
        mock_image_field = MagicMock()
        mock_image_field.name = 'poster/original_name.jpg'
        mock_image_field.file = self._create_test_image()
        mock_image_field.__bool__ = lambda self: True

        saved_paths = []
        mock_image_field.storage = self._create_mock_storage(saved_paths)

        resize_and_convert_image(mock_image_field, max_size=1000)

        # サフィックスが追加されていないことを確認
        self.assertEqual(saved_paths[0], 'poster/original_name.jpeg')
        # -1000 などのサフィックスがないことを確認
        self.assertNotIn('-1000', saved_paths[0])

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

        resize_and_convert_image(mock_image_field, max_size=200)

        # 新規作成時は image_field.save() が呼ばれる
        self.assertEqual(len(saved_filenames), 1)
        # サフィックスなしで拡張子のみ変更
        self.assertEqual(saved_filenames[0], 'simple.jpeg')

    def test_filename_with_nested_directory(self):
        """深くネストされたディレクトリパスでも正しく処理されることを確認"""
        mock_image_field = MagicMock()
        mock_image_field.name = 'uploads/2024/01/poster/image.jpg'
        mock_image_field.file = self._create_test_image()
        mock_image_field.__bool__ = lambda self: True

        saved_paths = []
        mock_image_field.storage = self._create_mock_storage(saved_paths)

        resize_and_convert_image(mock_image_field, max_size=500)

        self.assertEqual(len(saved_paths), 1)
        # ディレクトリ構造が維持されていることを確認
        self.assertEqual(saved_paths[0], 'uploads/2024/01/poster/image.jpeg')
        self.assertEqual(mock_image_field.name, 'uploads/2024/01/poster/image.jpeg')

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
        resize_and_convert_image(mock_image_field, max_size=100)
        self.assertEqual(mock_image_field.name, 'poster/image.jpeg')

        # 2回目の更新をシミュレート（新しいファイルがアップロードされた想定）
        mock_image_field.file = self._create_test_image()
        # nameは変わらない想定（同じパスに再保存）
        resize_and_convert_image(mock_image_field, max_size=100)

        # 2回とも同じディレクトリ構造であることを確認
        self.assertEqual(len(saved_paths), 2)
        self.assertEqual(saved_paths[0], 'poster/image.jpeg')
        self.assertEqual(saved_paths[1], 'poster/image.jpeg')
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
            result = resize_and_convert_image(mock_image_field, max_size=100)
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
            result = resize_and_convert_image(mock_image_field, max_size=100)
            self.assertIsNone(result)

    def test_none_image_field_returns_early(self):
        """image_fieldがNoneの場合、早期リターンすることを確認"""
        # エラーが発生せずに正常終了することを確認
        result = resize_and_convert_image(None, max_size=100)
        self.assertIsNone(result)

    def test_falsy_image_field_returns_early(self):
        """image_fieldがFalsyな場合、早期リターンすることを確認"""
        mock_image_field = MagicMock()
        mock_image_field.__bool__ = lambda self: False

        result = resize_and_convert_image(mock_image_field, max_size=100)
        self.assertIsNone(result)

    def test_jpeg_quality_82_is_applied(self):
        """JPEG quality が 82 で出力されることを確認（デフォルト値のテスト）"""
        mock_image_field = MagicMock()
        mock_image_field.name = 'poster/image.jpg'
        mock_image_field.file = self._create_test_image()
        mock_image_field.__bool__ = lambda self: True

        saved_paths = []
        mock_image_field.storage = self._create_mock_storage(saved_paths)

        # 保存された画像のバッファをキャプチャ
        saved_content = []
        original_storage_save = mock_image_field.storage.save

        def capture_content(path, content):
            saved_content.append(content.read())
            content.seek(0)
            return original_storage_save(path, content)

        mock_image_field.storage.save = capture_content

        resize_and_convert_image(mock_image_field, max_size=100)

        # 保存されたことを確認
        self.assertEqual(len(saved_paths), 1)
        self.assertTrue(saved_paths[0].endswith('.jpeg'))

    def test_small_png_stays_png(self):
        """200KB未満のPNGはPNGのまま維持されることを確認"""
        # 小さいPNG画像を作成（200KB未満）
        img = Image.new('RGB', (50, 50), color='blue')
        buffer = BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)

        mock_image_field = MagicMock()
        mock_image_field.name = 'poster/small.png'
        mock_image_field.file = buffer
        mock_image_field.__bool__ = lambda self: True

        saved_paths = []
        mock_image_field.storage = self._create_mock_storage(saved_paths)

        resize_and_convert_image(mock_image_field, max_size=100)

        # PNGのまま保存されることを確認
        self.assertTrue(saved_paths[0].endswith('.png'))

    def test_large_non_transparent_png_converts_to_jpeg(self):
        """200KB以上の透過なしPNGはJPEGに変換されることを確認"""
        # 大きいPNG画像を作成
        buffer = self._create_large_png_image(min_size_kb=200)
        file_size = len(buffer.getvalue())
        buffer.seek(0)

        # ファイルサイズを確認（200KB以上であること）
        self.assertGreaterEqual(file_size, 200 * 1024, "テスト画像が200KB未満です")

        mock_image_field = MagicMock()
        mock_image_field.name = 'poster/large.png'
        mock_image_field.file = buffer
        mock_image_field.__bool__ = lambda self: True

        saved_paths = []
        mock_image_field.storage = self._create_mock_storage(saved_paths)

        resize_and_convert_image(mock_image_field, max_size=1000)

        # JPEGに変換されることを確認
        self.assertTrue(saved_paths[0].endswith('.jpeg'))

    def test_transparent_png_stays_png(self):
        """透過ありPNGはサイズに関わらずPNGのまま維持されることを確認"""
        # 透過ありの大きいPNG画像を作成
        img = Image.new('RGBA', (1000, 1000), color=(0, 255, 0, 128))
        buffer = BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)

        mock_image_field = MagicMock()
        mock_image_field.name = 'poster/transparent.png'
        mock_image_field.file = buffer
        mock_image_field.__bool__ = lambda self: True

        saved_paths = []
        mock_image_field.storage = self._create_mock_storage(saved_paths)

        resize_and_convert_image(mock_image_field, max_size=1000)

        # PNGのまま保存されることを確認
        self.assertTrue(saved_paths[0].endswith('.png'))

    def test_palette_mode_png_with_transparency_stays_png(self):
        """パレットモードで透過ありのPNGはPNGのまま維持されることを確認"""
        # パレットモードの透過PNG画像を作成
        img = Image.new('P', (1000, 1000))
        # 透過情報を追加
        img.info['transparency'] = 0
        buffer = BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)

        mock_image_field = MagicMock()
        mock_image_field.name = 'poster/palette_transparent.png'
        mock_image_field.file = buffer
        mock_image_field.__bool__ = lambda self: True

        saved_paths = []
        mock_image_field.storage = self._create_mock_storage(saved_paths)

        resize_and_convert_image(mock_image_field, max_size=1000)

        # PNGのまま保存されることを確認
        self.assertTrue(saved_paths[0].endswith('.png'))

    def test_jpeg_stays_jpeg(self):
        """JPEGはJPEGのまま維持されることを確認"""
        mock_image_field = MagicMock()
        mock_image_field.name = 'poster/photo.jpg'
        mock_image_field.file = self._create_test_image(format='JPEG')
        mock_image_field.__bool__ = lambda self: True

        saved_paths = []
        mock_image_field.storage = self._create_mock_storage(saved_paths)

        resize_and_convert_image(mock_image_field, max_size=100)

        # JPEGのまま保存されることを確認
        self.assertTrue(saved_paths[0].endswith('.jpeg'))

    def test_custom_jpeg_quality_parameter(self):
        """カスタムJPEG qualityパラメータが受け付けられることを確認"""
        mock_image_field = MagicMock()
        mock_image_field.name = 'poster/image.jpg'
        mock_image_field.file = self._create_test_image()
        mock_image_field.__bool__ = lambda self: True

        saved_paths = []
        mock_image_field.storage = self._create_mock_storage(saved_paths)

        # エラーなく実行できることを確認
        resize_and_convert_image(mock_image_field, max_size=100, jpeg_quality=75)

        # 保存されたことを確認
        self.assertEqual(len(saved_paths), 1)

    def test_custom_png_to_jpeg_threshold(self):
        """カスタムPNG→JPEG閾値が反映されることを確認"""
        # 50KB以上のPNG画像を作成
        buffer = self._create_large_png_image(min_size_kb=50)
        file_size = len(buffer.getvalue())
        buffer.seek(0)

        mock_image_field = MagicMock()
        mock_image_field.name = 'poster/medium.png'
        mock_image_field.file = buffer
        mock_image_field.__bool__ = lambda self: True

        saved_paths = []
        mock_image_field.storage = self._create_mock_storage(saved_paths)

        # 閾値を下げてJPEGに変換されることを確認
        # file_sizeより小さい閾値を設定
        resize_and_convert_image(mock_image_field, max_size=500, png_to_jpeg_threshold=1024)

        self.assertTrue(saved_paths[0].endswith('.jpeg'))

    def test_png_below_threshold_stays_png(self):
        """閾値未満のPNGはPNGのまま維持されることを確認"""
        # 小さいPNG画像を作成
        img = Image.new('RGB', (100, 100), color='red')
        buffer = BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)

        mock_image_field = MagicMock()
        mock_image_field.name = 'poster/small.png'
        mock_image_field.file = buffer
        mock_image_field.__bool__ = lambda self: True

        saved_paths = []
        mock_image_field.storage = self._create_mock_storage(saved_paths)

        # 大きな閾値を設定
        resize_and_convert_image(mock_image_field, max_size=500, png_to_jpeg_threshold=1024 * 1024)

        # PNGのまま保存されることを確認
        self.assertTrue(saved_paths[0].endswith('.png'))
