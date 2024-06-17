import os
from django.db import models
from django.core.files.base import ContentFile
from PIL import Image
from io import BytesIO


def resize_and_convert_image(image_field, max_size=720, output_format='JPEG'):
    """
    画像をリサイズして指定のフォーマットに変換する関数

    Args:
        image_field (ImageFieldFile): 画像フィールド
        max_size (int): 最大サイズ (デフォルトは720px)
        output_format (str): 出力フォーマット (デフォルトは'JPEG')

    Returns:
        None
    """
    if image_field:
        img = Image.open(image_field.file)

        # 画像のモードがパレットモード('P')の場合、RGBモードに変換する
        if img.mode == 'P':
            img = img.convert('RGB')

        # 画像のサイズを取得
        width, height = img.size

        # 縦横比を維持しながらリサイズ
        if width > max_size or height > max_size:
            if width > height:
                new_width = max_size
                new_height = int(height * (max_size / width))
            else:
                new_width = int(width * (max_size / height))
                new_height = max_size

            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

        # リサイズした画像で置き換え
        buffer = BytesIO()
        img.save(buffer, format=output_format, optimize=True, quality=85)
        buffer.seek(0)

        # ファイル名の拡張子を変更
        file_name, _ = os.path.splitext(image_field.name)
        file_name = f"{file_name}-{max_size}.{output_format.lower()}"

        image_field.save(file_name, ContentFile(buffer.read()), save=False)
