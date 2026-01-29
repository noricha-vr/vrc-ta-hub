import os
from io import BytesIO

from PIL import Image
from django.core.files.base import ContentFile


def resize_and_convert_image(image_field, max_size=720, output_format='JPEG'):
    """
    画像をリサイズして指定のフォーマットに変換する関数

    新規作成時と更新時で保存方法を分けることで、upload_to によるパスの
    二重追加を防ぐ。

    - 新規作成時: image_field.name にディレクトリがない
      → image_field.save() を使用（upload_to が正しく適用される）
    - 更新時: image_field.name に既にディレクトリパスがある
      → 直接ストレージに保存（upload_to の二重適用を防ぐ）

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

        # 画像のモードがRGBAの場合、RGBに変換
        if img.mode == 'RGBA':
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

        # 現在のパスからディレクトリとファイル名を分離
        current_path = image_field.name
        dir_name = os.path.dirname(current_path)
        base_name = os.path.basename(current_path)
        file_name, _ = os.path.splitext(base_name)
        new_file_name = f"{file_name}-{max_size}.{output_format.lower()}"

        content = ContentFile(buffer.read())

        # ディレクトリパスがある = 既に保存済み（更新時）
        # → 直接ストレージに保存してupload_toの二重適用を防ぐ
        if dir_name:
            new_path = f"{dir_name}/{new_file_name}"

            storage = image_field.storage

            # 既存ファイルを削除
            if current_path and storage.exists(current_path):
                try:
                    storage.delete(current_path)
                except Exception:
                    pass  # 削除に失敗しても続行

            # 新しいファイルを保存
            saved_name = storage.save(new_path, content)

            # ImageFieldのnameを直接更新
            image_field.name = saved_name
        else:
            # ディレクトリパスがない = 新規作成時
            # → image_field.save() を使用してupload_toを適用
            image_field.save(new_file_name, content, save=False)
