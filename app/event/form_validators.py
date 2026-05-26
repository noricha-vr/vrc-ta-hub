"""Event フォームで使うファイル検証処理."""

import logging
from io import BytesIO
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, UnidentifiedImageError

from website.constants import MAX_PDF_SIZE_BYTES, MAX_THUMBNAIL_SIZE_BYTES

from .models import validate_pdf_file
from .thumbnail import crop_to_slide_thumbnail_aspect_ratio

logger = logging.getLogger(__name__)


def validate_thumbnail_image(thumbnail_image):
    """サムネイル画像を検証し、スライド比率に中央クロップする."""
    if not thumbnail_image or not hasattr(thumbnail_image, 'read'):
        return thumbnail_image
    if getattr(thumbnail_image, 'size', 0) > MAX_THUMBNAIL_SIZE_BYTES:
        raise ValidationError('画像ファイルサイズが10MBを超えています。')

    try:
        with Image.open(thumbnail_image) as image:
            cropped_image = crop_to_slide_thumbnail_aspect_ratio(image.convert('RGB'))
            image_buffer = BytesIO()
            cropped_image.save(image_buffer, format='JPEG', quality=90, optimize=True)
    except (UnidentifiedImageError, OSError):
        raise ValidationError('有効な画像ファイルをアップロードしてください。')
    finally:
        try:
            thumbnail_image.seek(0)
        except (AttributeError, OSError, ValueError):
            logger.exception(
                "サムネイル画像の読み取り位置リセットに失敗しました: name=%s",
                getattr(thumbnail_image, 'name', None),
            )

    filename = f"{Path(thumbnail_image.name).stem}.jpg"
    return ContentFile(image_buffer.getvalue(), name=filename)


def validate_and_sanitize_pdf(slide_file):
    """PDFファイルを検証し、フォーム保存用に content_type を補正する."""
    if not slide_file:
        return slide_file
    if not slide_file.name.lower().endswith('.pdf'):
        raise ValidationError('PDFファイルのみアップロード可能です。')
    if slide_file.size > MAX_PDF_SIZE_BYTES:
        raise ValidationError('ファイルサイズが30MBを超えています。')
    validate_pdf_file(slide_file)
    try:
        slide_file.content_type = 'application/pdf'
    except (AttributeError, TypeError):
        logger.exception(
            "PDFアップロードのcontent_type設定に失敗しました: name=%s",
            getattr(slide_file, 'name', None),
        )
    return slide_file
