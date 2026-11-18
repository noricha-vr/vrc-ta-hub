"""生成記事の H2 の直下に、公開ストレージのスライド画像を挿入する。"""
from __future__ import annotations

import logging
import os
import re
import uuid
from typing import TYPE_CHECKING
from urllib.parse import quote

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from event.models import EventDetail
from event.pdf_processing import MAX_PDF_TEXT_PAGES
from event.services.pdf_worker import run_pdf_worker

if TYPE_CHECKING:
    from event.services.content_generation_service import BlogOutput, BlogSources

logger = logging.getLogger(__name__)
MAX_ARTICLE_SLIDE_IMAGES = 8


def _h2_lines(text: str):
    """コードブロックを除いた H2 の行番号・見出しを返す。"""
    fence = None
    for index, line in enumerate(text.splitlines()):
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if marker:
            value = marker.group(1)
            if fence is None:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence):
                fence = None
            continue
        if fence is None:
            heading = re.match(r'^ {0,3}##[ \t]+(.+?)[ \t]*$', line)
            if heading:
                yield index, re.sub(r'[ \t]+#+$', '', heading.group(1)).strip()


def _source_is_current(event_detail: EventDetail, slide_name: str) -> bool:
    """画像公開の直前にも、記事化 NG と PDF の差し替えを確かめる。"""
    if event_detail.is_article_ng:
        return False
    if not event_detail.pk:
        return True
    current = EventDetail.all_objects.filter(pk=event_detail.pk).first()
    return bool(current and not current.is_article_ng and current.article_pdf
                and current.article_pdf.name == slide_name)


def _render_images(event_detail: EventDetail, selected, slide_name: str) -> dict[int, str]:
    """PDF のコピーは一度だけ行い、ページ単位の失敗は省略する。"""
    from event.services.content_generation_service import _copy_uploaded_file_to_temp_path

    temp_path = None
    images = {}
    page_urls = {}
    directory = uuid.uuid4()
    try:
        temp_path = _copy_uploaded_file_to_temp_path(event_detail.article_pdf)
        for index, heading, page in selected:
            try:
                if page not in page_urls:
                    image = run_pdf_worker('page_image', temp_path, page=page)
                    if not image or not _source_is_current(event_detail, slide_name):
                        continue
                    name = default_storage.save(f'slide_page/{directory}/{page}.jpg', ContentFile(image))
                    page_urls[page] = default_storage.url(name)
                alt = re.sub(r'([\\\[\]<>`*_])', r'\\\1', heading)
                url = quote(page_urls[page], safe=':/?=&%#@+~-._')
                images[index] = f'\n\n![{alt}のスライド]({url})\n'
            except Exception:
                logger.warning('article_slide_image_failed', extra={
                    'event_type': 'article_slide_image_failed', 'event_detail_id': event_detail.pk, 'page': page,
                })
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
    return images


def insert_slide_images(event_detail: EventDetail, output: BlogOutput, sources: BlogSources) -> str:
    """LLM のページ指定を使い、上限内の見出しに画像を入れる。失敗しても本文は返す。"""
    pdf = event_detail.article_pdf
    if not pdf or not sources.slide_name or pdf.name != sources.slide_name or not output.slide_pages:
        return output.text
    mapping: dict[str, int | None] = {}
    for item in output.slide_pages:
        mapping.setdefault(item.heading, item.page)
    selected = []
    for index, heading in _h2_lines(output.text):
        page = mapping.get(heading)
        if page is not None and 1 <= page <= MAX_PDF_TEXT_PAGES:
            selected.append((index, heading, page))
        if len(selected) >= MAX_ARTICLE_SLIDE_IMAGES:
            break
    images = {}
    try:
        if selected and _source_is_current(event_detail, sources.slide_name):
            images = _render_images(event_detail, selected, sources.slide_name)
    except Exception:
        logger.warning('article_slide_images_failed', extra={
            'event_type': 'article_slide_images_failed', 'event_detail_id': event_detail.pk,
        })
    logger.info('article_slide_images_result', extra={
        'event_type': 'article_slide_images_result', 'event_detail_id': event_detail.pk,
        'requested_count': len(selected), 'inserted_count': len(images),
    })
    return '\n'.join(line + images.get(index, '') for index, line in enumerate(output.text.split('\n')))
