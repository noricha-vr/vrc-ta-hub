"""Markdown↔HTML 変換とサニタイズを担うモジュール.

`convert_markdown` の各処理段階を独立した関数に分け、
1関数あたり50行・ネスト4階層以内に収める。
"""
from __future__ import annotations

import copy
import logging
import re
from urllib.parse import urlparse

import bleach
import markdown
from bleach.css_sanitizer import CSSSanitizer
from bs4 import BeautifulSoup

from event.services.content_sanitizer import (
    ALLOWED_IFRAME_DOMAINS,
    _escape_unknown_html_tags,
    _filter_iframe_attributes,
)
from website.constants import is_site_domain

logger = logging.getLogger(__name__)

_YOUTUBE_PATTERNS = (
    (r'https?://www\.youtube\.com/watch\?v=([a-zA-Z0-9_-]+)', r'https://www.youtube.com/embed/\1'),
    (r'https?://youtu\.be/([a-zA-Z0-9_-]+)', r'https://www.youtube.com/embed/\1'),
)
_PLAIN_URL_PATTERN = re.compile(r'https?://[^\s<>"\']+[^\s<>"\'.,;:!?)）」】]')

_LIST_PREFIXES = ('- ', '* ', '+ ', '1. ', '2. ', '3. ')
_EMOJI_PATTERN = r'[\U0001F300-\U0001F9FF\u200d\u2600-\u26FF\u2700-\u27BF]'

_ALLOWED_TAGS = [
    'a', 'p', 'h1', 'h2', 'h3', 'h4', 'ul', 'ol', 'li', 'strong', 'em',
    'code', 'pre', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'hr', 'br',
    'blockquote', 'div', 'iframe', 'button', 'img', 'i', 'span',
]
_ALLOWED_CSS_PROPERTIES = [
    'display', 'flex', 'flex-wrap', 'gap', 'text-align', 'height', 'width',
    'object-fit', 'margin', 'margin-top', 'margin-bottom', 'margin-left', 'margin-right',
    'padding', 'padding-top', 'padding-bottom', 'padding-left', 'padding-right',
    'justify-content', 'align-items', 'max-width', 'max-height',
    'background', 'background-color', 'color', 'font-size', 'font-weight',
    'border', 'border-radius',
]
_MARKDOWN_EXTENSIONS = ['tables', 'nl2br', 'fenced_code']


def _build_allowed_attributes() -> dict:
    """bleach.clean に渡す属性ホワイトリストを構築する."""
    return {
        'a': ['href', 'title', 'download', 'class'],
        'pre': ['class'],
        'table': ['class'],
        'div': ['class', 'style'],
        'button': ['style', 'class'],
        'img': ['src', 'alt', 'style', 'class'],
        'i': ['class'],
        'span': ['class', 'style'],
        'iframe': _filter_iframe_attributes,
    }


def _normalize_markdown_text(text: str) -> str:
    """改行を統一し、先頭の H1 行を除去する."""
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    lines = text.split('\n')
    if lines and lines[0].strip().startswith('# '):
        lines = lines[1:]
        return '\n'.join(lines).lstrip()
    return text


def _split_sentence_into_lines(line: str) -> list[str]:
    """非リスト行を句読点で分割し、各文の後に空行を挿入する."""
    sentences = re.split(r'([。！？])', line.lstrip())
    out: list[str] = []
    for i in range(0, len(sentences) - 1, 2):
        if sentences[i].strip():
            suffix = sentences[i + 1] if i + 1 < len(sentences) else ''
            out.append(sentences[i] + suffix)
            out.append('')
    if sentences[-1].strip() and sentences[-1][-1] not in '。！？':
        out.append(sentences[-1])
    return out


def _auto_format_markdown(text: str) -> str:
    """auto_format=True 時の整形処理."""
    normalized: list[str] = []
    in_list = False
    for line in text.split('\n'):
        if line.lstrip().startswith(_LIST_PREFIXES):
            in_list = True
            normalized.append(line)
        elif line.strip() == '':
            in_list = False
            normalized.append(line)
        elif in_list:
            normalized.append(line)
        else:
            normalized.extend(_split_sentence_into_lines(line))

    text = '\n'.join(normalized)
    text = re.sub(r'\n{3,}', '\n\n', text)
    # 感嘆符・疑問符と閉じ括弧または絵文字の間の改行を削除
    return re.sub(
        rf'([！!？?])\n+((?:{_EMOJI_PATTERN}+|[」）\)]))',
        r'\1\2',
        text,
    )


def _apply_table_classes(soup: BeautifulSoup) -> None:
    """table 要素に Bootstrap クラスを付与する."""
    for table in soup.find_all('table'):
        table['class'] = table.get('class', []) + ['table', 'table-responsive']


def _linkify_youtube_urls_in_text(soup: BeautifulSoup) -> None:
    """p / li 要素のテキスト内 YouTube URL を <a> タグに変換する."""
    for p in soup.find_all(['p', 'li']):
        if not p.get_text():
            continue
        new_contents = []
        for content in p.contents:
            if not isinstance(content, str):
                new_contents.append(copy.copy(content))
                continue
            text, modified = _wrap_youtube_urls_with_anchor(content)
            if modified:
                temp_soup = BeautifulSoup(text, 'html.parser')
                new_contents.extend(temp_soup.contents)
            else:
                new_contents.append(content)
        p.clear()
        for content in new_contents:
            p.append(content)


def _wrap_youtube_urls_with_anchor(text: str) -> tuple[str, bool]:
    """テキスト内の YouTube URL を <a> タグで囲む."""
    modified = False
    for pattern, _ in _YOUTUBE_PATTERNS:
        if re.search(pattern, text):
            text = re.sub(pattern, r'<a href="\g<0>">\g<0></a>', text)
            modified = True
    return text, modified


def _linkify_plain_urls(soup: BeautifulSoup) -> None:
    """段落・リスト・見出し内の平文 URL を <a> タグに変換する.

    YouTube URL は既に <a> 化済みなので重複処理しない。
    既存の <a> / <code> / <pre> の中は触らない。
    """
    targets = soup.find_all(['p', 'li', 'h1', 'h2', 'h3', 'h4', 'blockquote'])
    for elem in targets:
        for text_node in list(elem.find_all(string=True)):
            if text_node.find_parent(['a', 'code', 'pre']):
                continue
            if not _PLAIN_URL_PATTERN.search(str(text_node)):
                continue
            replacement = _PLAIN_URL_PATTERN.sub(
                lambda m: f'<a href="{m.group(0)}">{m.group(0)}</a>',
                str(text_node),
            )
            new_nodes = BeautifulSoup(replacement, 'html.parser')
            text_node.replace_with(new_nodes)


def _convert_youtube_anchors_to_iframes(soup: BeautifulSoup) -> None:
    """YouTube 形式の <a href> を埋め込み iframe に置き換える."""
    for link in soup.find_all('a', href=True):
        href = link.get('href', '')
        for pattern, embed_url_pattern in _YOUTUBE_PATTERNS:
            if not re.match(pattern, href):
                continue
            container = soup.new_tag('div', **{'class': 'youtube-embed-container'})
            iframe = soup.new_tag(
                'iframe',
                src=re.sub(pattern, embed_url_pattern, href),
                frameborder='0',
                allowfullscreen=True,
                referrerpolicy='strict-origin-when-cross-origin',
                allow='accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share',
            )
            container.append(iframe)
            link.replace_with(container)
            break


def _is_disallowed_iframe_src(src: str) -> bool:
    """iframe の src がドメイン制約に違反する場合に True を返す."""
    if not src:
        return True
    try:
        parsed = urlparse(src)
    except (TypeError, ValueError):
        logger.exception("iframe srcのURL解析に失敗したため削除します: src=%r", src)
        return True
    if parsed.scheme not in ('http', 'https'):
        return True
    if is_site_domain(parsed.hostname):
        return True
    return parsed.netloc not in ALLOWED_IFRAME_DOMAINS


def _remove_disallowed_iframes(soup: BeautifulSoup) -> None:
    """bleach の属性フィルタだけでは残る空 iframe を DOM から除去する."""
    for iframe in soup.find_all('iframe'):
        if _is_disallowed_iframe_src(iframe.get('src', '')):
            iframe.decompose()


def _sanitize_html(html: str) -> str:
    """bleach を使って許可タグ・属性・CSS のみの HTML に整える."""
    css_sanitizer = CSSSanitizer(allowed_css_properties=_ALLOWED_CSS_PROPERTIES)
    return bleach.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_build_allowed_attributes(),
        css_sanitizer=css_sanitizer,
    )


def convert_markdown(markdown_text: str, auto_format: bool = False) -> str:
    """MarkdownをHTMLに変換し、サニタイズする.

    Args:
        markdown_text: 変換するMarkdownテキスト
        auto_format: 句読点や絵文字を起点とした自動整形を行うかどうか
    """
    logger.debug("Original markdown text:")
    logger.debug(markdown_text)

    markdown_text = _escape_unknown_html_tags(markdown_text)
    markdown_text = _normalize_markdown_text(markdown_text)

    if auto_format:
        markdown_text = _auto_format_markdown(markdown_text)
    else:
        markdown_text = re.sub(r'\n{3,}', '\n\n', markdown_text)

    logger.debug("Normalized markdown text:")
    logger.debug(markdown_text)

    html = markdown.markdown(markdown_text, extensions=_MARKDOWN_EXTENSIONS)
    logger.debug("Generated HTML before BeautifulSoup:")
    logger.debug(html)

    soup = BeautifulSoup(html, 'html.parser')
    _apply_table_classes(soup)
    _linkify_youtube_urls_in_text(soup)
    _convert_youtube_anchors_to_iframes(soup)
    _linkify_plain_urls(soup)
    _remove_disallowed_iframes(soup)

    html = str(soup)
    logger.debug("Generated HTML before sanitization:")
    logger.debug(html)

    sanitized_html = _sanitize_html(html)
    logger.debug("Final sanitized HTML:")
    logger.debug(sanitized_html)
    return sanitized_html
