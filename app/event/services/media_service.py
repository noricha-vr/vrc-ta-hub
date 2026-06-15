"""PDFサムネイル生成を提供するモジュール."""
from __future__ import annotations

import logging
import os
import tempfile
from io import BytesIO

import pypdfium2 as pdfium
from django.core.files.base import ContentFile

from event.models import EventDetail
from event.thumbnail import crop_to_slide_thumbnail_aspect_ratio

logger = logging.getLogger(__name__)

PDF_THUMBNAIL_MAX_RENDER_SCALE = 2.0
PDF_THUMBNAIL_MAX_LONG_EDGE_PX = 1600


def _get_pdf_thumbnail_render_scale(page) -> float:
    """PDFページの長辺が上限を超えないレンダリング倍率を返す."""
    try:
        width, height = page.get_size()
        long_edge = max(float(width), float(height))
    except (AttributeError, TypeError, ValueError):
        return PDF_THUMBNAIL_MAX_RENDER_SCALE

    if long_edge <= 0:
        return PDF_THUMBNAIL_MAX_RENDER_SCALE

    return min(PDF_THUMBNAIL_MAX_RENDER_SCALE, PDF_THUMBNAIL_MAX_LONG_EDGE_PX / long_edge)


def ensure_pdf_thumbnail(event_detail: EventDetail, *, save: bool = False, overwrite: bool = False) -> bool:
    """PDFの先頭ページから未設定のサムネイル画像を作成する.

    Args:
        event_detail: サムネイルを設定するイベント詳細
        save: Trueの場合はthumbnail_imageだけを保存する
        overwrite: Trueの場合は既存のthumbnail_imageがあっても再生成する

    Returns:
        サムネイルを新規作成した場合はTrue
    """
    if (event_detail.thumbnail_image and not overwrite) or not event_detail.slide_file:
        return False

    temp_file_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
            event_detail.slide_file.open('rb')
            for chunk in event_detail.slide_file.chunks():
                temp_file.write(chunk)
            temp_file_path = temp_file.name

        pdf = pdfium.PdfDocument(temp_file_path)
        try:
            page = pdf[0]
            try:
                bitmap = page.render(scale=_get_pdf_thumbnail_render_scale(page))
                try:
                    image = crop_to_slide_thumbnail_aspect_ratio(bitmap.to_pil().convert('RGB'))
                finally:
                    if hasattr(bitmap, 'close'):
                        bitmap.close()
            finally:
                if hasattr(page, 'close'):
                    page.close()
        finally:
            if hasattr(pdf, 'close'):
                pdf.close()

        image_buffer = BytesIO()
        image.save(image_buffer, format='JPEG', quality=85, optimize=True)
        filename = f"event_detail_{event_detail.pk or 'new'}_thumbnail.jpg"
        event_detail.thumbnail_image.save(filename, ContentFile(image_buffer.getvalue()), save=False)
        if save:
            event_detail.save(update_fields=['thumbnail_image'])
        return True
    except Exception:
        logger.exception("PDFサムネイルの生成に失敗しました: EventDetail ID=%s", event_detail.pk)
        return False
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)
