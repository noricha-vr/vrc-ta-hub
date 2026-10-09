"""PDFサムネイル生成を提供するモジュール."""
from __future__ import annotations

import logging
import os
import tempfile

from django.core.files.base import ContentFile
from django.db.models import Q

from event.models import EventDetail
from event.services.pdf_worker import PdfWorkerError, run_pdf_worker

logger = logging.getLogger(__name__)


def ensure_pdf_thumbnail(event_detail: EventDetail, *, save: bool = False, overwrite: bool = False) -> bool:
    """PDFの先頭ページから未設定のサムネイル画像を作成する.

    Args:
        event_detail: サムネイルを設定するイベント詳細
        save: Trueの場合はthumbnail_imageだけを保存する。overwrite でなければ、今の行のサムネイルが
            空の時だけ書く（作っている間に利用者が上げた画像を上書きしない）
        overwrite: Trueの場合は既存のthumbnail_imageがあっても再生成する

    Returns:
        サムネイルを新規作成した場合はTrue（今の行に画像があって書かなかった時はFalse）
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
        if not save:
            return True
        if overwrite:
            event_detail.save(update_fields=['thumbnail_image'])
            return True
        return _store_thumbnail_if_empty(event_detail)
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


def _store_thumbnail_if_empty(event_detail: EventDetail) -> bool:
    """作った画像を、今の行のサムネイルが空の時だけ書く（条件付き UPDATE）。

    PDF から画像を作る間（数秒）に利用者がサムネイルを上げることがあるので、読み込んだ時の値ではなく
    書く時の行の値で決める。書けなかった時は、作った画像をストレージから消して孤児にしない。
    UPDATE はシグナルを出さないが、サムネイルだけの保存に反応するシグナルは無い
    （トップページのキャッシュはサムネイルを含まず、ツイートの同期は発表者・テーマ・入力の変更だけを見る）。

    Returns:
        書いた時 True
    """
    created_name = event_detail.thumbnail_image.name
    stored = EventDetail.all_objects.filter(
        Q(thumbnail_image='') | Q(thumbnail_image__isnull=True), pk=event_detail.pk,
    ).update(thumbnail_image=created_name)
    if stored:
        return True

    current_name = (
        EventDetail.all_objects.filter(pk=event_detail.pk).values_list('thumbnail_image', flat=True).first()
    )
    if created_name and created_name != current_name:
        event_detail.thumbnail_image.storage.delete(created_name)
    # 呼び出し側（ツイートの画像の同期など）が今の行の画像を使えるよう、メモリ上の値も揃える
    event_detail.thumbnail_image = current_name or None
    logger.info(
        'pdf_thumbnail_discarded',
        extra={'event_type': 'pdf_thumbnail_discarded', 'event_detail_id': event_detail.pk},
    )
    return False
