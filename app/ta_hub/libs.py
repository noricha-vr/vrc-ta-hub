import os
from io import BytesIO

from PIL import Image
from django.core.files.base import ContentFile

# 定数定義
DEFAULT_MAX_SIZE = 1000
DEFAULT_JPEG_QUALITY = 82
DEFAULT_PNG_TO_JPEG_THRESHOLD = 200 * 1024  # 200KB


def resize_and_convert_image(
    image_field,
    max_size=DEFAULT_MAX_SIZE,
    jpeg_quality=DEFAULT_JPEG_QUALITY,
    png_to_jpeg_threshold=DEFAULT_PNG_TO_JPEG_THRESHOLD
):
    """
    画像をリサイズし、必要に応じてフォーマットを変換する関数

    PNG→JPEG変換の条件:
    - ファイルサイズが png_to_jpeg_threshold 以上
    - 透過がない場合のみ

    Args:
        image_field (ImageFieldFile): 画像フィールド
        max_size (int): 最大サイズ (デフォルトは1000px)
        jpeg_quality (int): JPEG圧縮品質 (デフォルトは82)
        png_to_jpeg_threshold (int): PNG→JPEG変換の閾値バイト数 (デフォルトは200KB)

    Returns:
        None
    """
    if not image_field:
        return

    # ファイルを開く（存在しない場合はスキップ）
    try:
        img = Image.open(image_field.file)
    except FileNotFoundError:
        # ストレージ上にファイルが存在しない場合はスキップ
        return
    except Exception:
        # その他のエラーもスキップ（壊れたファイル等）
        return

    # 元のフォーマットとサイズを記録
    original_format = img.format or 'JPEG'

    # ファイルサイズを取得（BytesIOの場合はサイズを計算）
    try:
        image_field.file.seek(0, 2)  # ファイル末尾にシーク
        original_size = image_field.file.tell()
        image_field.file.seek(0)  # ファイル先頭に戻す
    except Exception:
        original_size = 0

    # 透過チェック
    has_transparency = img.mode in ('RGBA', 'LA') or (
        img.mode == 'P' and 'transparency' in img.info
    )

    # PNG→JPEG変換の判断
    is_png = original_format == 'PNG'
    should_convert_to_jpeg = (
        is_png and
        original_size >= png_to_jpeg_threshold and
        not has_transparency
    )

    # 出力フォーマットを決定
    if should_convert_to_jpeg:
        output_format = 'JPEG'
    else:
        # 元のフォーマットを維持（ただしJPEGとPNG以外はJPEGに統一）
        output_format = original_format if original_format in ('JPEG', 'PNG') else 'JPEG'

    # RGBモードへの変換（JPEG出力の場合のみ）
    if output_format == 'JPEG':
        if img.mode == 'P':
            img = img.convert('RGB')
        if img.mode == 'RGBA':
            img = img.convert('RGB')
        if img.mode == 'LA':
            img = img.convert('L').convert('RGB')

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
    if output_format == 'JPEG':
        img.save(buffer, format=output_format, optimize=True, quality=jpeg_quality)
    else:
        # PNGの場合はoptimize=Trueのみ
        img.save(buffer, format=output_format, optimize=True)
    buffer.seek(0)

    # 現在のパスからディレクトリとファイル名を分離
    current_path = image_field.name
    dir_name = os.path.dirname(current_path)
    base_name = os.path.basename(current_path)
    file_name, _ = os.path.splitext(base_name)

    # ファイル名にサフィックスは追加しない（元のファイル名を維持）
    new_extension = output_format.lower()
    new_file_name = f"{file_name}.{new_extension}"

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
