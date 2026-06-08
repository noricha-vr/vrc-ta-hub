"""[DEPRECATED] event.libs

PR1〜PR3 で services/ 配下に分割中の互換シム。次PRで削除する。
"""
from event.services.content_generation_service import (
    BlogOutput,
    apply_blog_output_to_event_detail,
    generate_blog,
    get_transcript,
)
from event.services.content_sanitizer import (
    ALLOWED_IFRAME_DOMAINS,
    _escape_unknown_html_tags,
    _filter_iframe_attributes,
)
from event.services.markdown_processor import convert_markdown
from event.services.media_service import ensure_pdf_thumbnail

__all__ = [
    'ALLOWED_IFRAME_DOMAINS',
    'BlogOutput',
    'apply_blog_output_to_event_detail',
    'convert_markdown',
    'ensure_pdf_thumbnail',
    'generate_blog',
    'get_transcript',
    '_escape_unknown_html_tags',
    '_filter_iframe_attributes',
]
