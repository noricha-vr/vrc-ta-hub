"""PDFサムネイル生成を提供するモジュール."""
from __future__ import annotations

import logging
import os
import tempfile

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q

from event.models import (
    EventDetail,
    recent_event_details_cache_key,
    related_event_details_cache_key,
    speaker_event_details_cache_key,
)
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
    if (event_detail.thumbnail_image and not overwrite) or not event_detail.article_pdf:
        return False
    # 画像を作る間に PDF が差し替えられたら、古い PDF の画像を書かないよう、元の PDF の名前を覚えておく
    source_slide_name = event_detail.article_pdf.name or ''
    # 非公開の PDF から作る画像は公開されるので、発表者が記事化を断った時は作らない
    if _is_private_source(event_detail, source_slide_name) and event_detail.is_article_ng:
        return False

    temp_file_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
            temp_file_path = temp_file.name
            event_detail.article_pdf.open('rb')
            try:
                for chunk in event_detail.article_pdf.chunks():
                    temp_file.write(chunk)
            finally:
                close = getattr(event_detail.article_pdf, 'close', None)
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
        return _store_thumbnail_if_empty(event_detail, source_slide_name)
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


def _is_private_source(event_detail: EventDetail, pdf_name: str) -> bool:
    return bool(event_detail.slide_source_file) and event_detail.slide_source_file.name == pdf_name


def _clear_related_card_caches(event_detail: EventDetail) -> None:
    """関連記事のカード（集会・発表者・サイト全体の新着）のキャッシュを、コミットの後に消す。"""
    keys = [
        related_event_details_cache_key(event_detail.event.community_id),
        recent_event_details_cache_key(),
    ]
    if event_detail.speaker:
        keys.append(speaker_event_details_cache_key(event_detail.speaker))
    transaction.on_commit(lambda: cache.delete_many(keys))


def _store_thumbnail_if_empty(event_detail: EventDetail, source_slide_name: str) -> bool:
    """作った画像を、今の行のサムネイルが空で、PDF が作り始めた時のままの時だけ書く（条件付き UPDATE）。

    PDF から画像を作る間（数秒）に利用者がサムネイルを上げることがあるので、読み込んだ時の値ではなく
    書く時の行の値で決める。書けなかった時は、作った画像をストレージから消して孤児にしない。
    UPDATE はシグナルを出さない（トップページのキャッシュはサムネイルを含まず、ツイートの同期は発表者・テーマ・
    入力の変更だけを見る）。発表記事ページの関連記事のカードはサムネイルを含むので、書いた後にそのキャッシュだけ消す。

    Returns:
        書いた時 True
    """
    created_name = event_detail.thumbnail_image.name
    if _is_private_source(event_detail, source_slide_name):
        # 画像を作る間に記事化 NG へ変わった時も書かない
        source_q = Q(slide_source_file=source_slide_name) & ~Q(article_consent=EventDetail.ArticleConsent.NG)
    else:
        source_q = Q(slide_file=source_slide_name)
    stored = EventDetail.all_objects.filter(
        source_q,
        Q(thumbnail_image='') | Q(thumbnail_image__isnull=True),
        pk=event_detail.pk,
    ).update(thumbnail_image=created_name)
    if stored:
        _clear_related_card_caches(event_detail)
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
