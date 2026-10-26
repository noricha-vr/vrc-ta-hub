"""サニタイズ後のガイド HTML に配信 URL と画像の表示属性を付ける。"""
import posixpath
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from django.templatetags.static import static


def is_public_path(path: str) -> bool:
    """内部資料はディレクトリ名も含めて公開しない。大文字小文字を区別しないファイルシステムでも AGENTS を拒否する。"""
    parts = PurePosixPath(path).parts
    return bool(parts) and all(not part.startswith('_') and part not in ('.', '..', '/') for part in parts) and PurePosixPath(path).stem.casefold() != 'agents'


def guide_link(href: str, page_path: str) -> str:
    """本文の相対 .md リンクをガイド URL に直す。アンカー等は保持する。"""
    url = urlsplit(href)
    if url.scheme or url.netloc or url.path.startswith('/') or not url.path.endswith('.md'):
        return href
    target = posixpath.normpath(posixpath.join(posixpath.dirname(page_path), unquote(url.path)))[:-3]
    if not is_public_path(target):
        return href
    return urlunsplit(('', '', f'/guide/{target}/', url.query, url.fragment))


def render_guide_html(html: str, page_path: str) -> str:
    """bleach 後に実行する。入力 HTML 自体のサニタイズは呼び出し側の責務。"""
    soup = BeautifulSoup(html, 'html.parser')
    for link in soup.find_all('a', href=True):
        link['href'] = guide_link(str(link['href']), page_path)
    for img in soup.find_all('img', src=True):
        src = str(img['src'])
        url = urlsplit(src)
        if url.scheme or url.netloc or not url.path.startswith('images/'):
            continue
        image_path = posixpath.normpath(unquote(url.path))
        if not image_path.startswith('images/') or '\\' in image_path:
            continue
        image_url = static(f'guide/{image_path}')
        img['src'] = image_url
        img['loading'] = 'lazy'
        img['class'] = ' '.join(dict.fromkeys([*img.get_attribute_list('class'), 'guide-screenshot']))
        if not img.find_parent('a'):
            link = soup.new_tag('a', href=image_url, target='_blank', rel='noopener')
            img.wrap(link)
    return str(soup)
