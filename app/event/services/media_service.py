"""PDFサムネイル生成を提供するモジュール."""
from __future__ import annotations

import logging
import os
import tempfile

from django.core.files.base import ContentFile

from event.models import EventDetail
from event.services.pdf_worker import PdfWorkerError, run_pdf_worker

logger = logging.getLogger(__name__)


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
            temp_file_path = temp_file.name
            event_detail.slide_file.open('rb')
            try:
                for chunk in event_detail.slide_file.chunks():
                    temp_file.write(chunk)
            finally:
                close = getattr(event_detail.slide_file, 'close', None)
                if callable(close):
                    close()

        image_bytes = run_pdf_worker("thumbnail", temp_file_path)
        filename = f"event_detail_{event_detail.pk or 'new'}_thumbnail.jpg"
        event_detail.thumbnail_image.save(filename, ContentFile(image_bytes), save=False)
        if save:
            event_detail.save(update_fields=['thumbnail_image'])
        return True
    except PdfWorkerError:
        # Keep thumbnail failure non-fatal, as before; the worker emits a fixed
        # structured result event with a low-cardinality failure reason.
        return False
    except Exception:
        logger.exception("PDFサムネイルの生成に失敗しました: EventDetail ID=%s", event_detail.pk)
        return False
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)
