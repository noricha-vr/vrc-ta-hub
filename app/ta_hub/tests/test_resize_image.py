import os
from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from ta_hub.models import ImageFile
from PIL import Image


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
