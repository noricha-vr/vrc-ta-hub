"""サニタイズ後のガイド HTML に配信 URL と画像の表示属性を付ける。"""
import hashlib
import posixpath
import re
from functools import lru_cache
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from django.templatetags.static import static
from PIL import Image

STATIC_GUIDE_ROOT = Path(__file__).resolve().parent / 'static' / 'guide'
# Bootstrap の sm 未満（スマホ幅）。この幅では -sp 版の画像に差し替える。
MOBILE_MEDIA = '(max-width: 575.98px)'


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


@lru_cache(maxsize=None)
def content_digest(path: str) -> str | None:
    file = STATIC_GUIDE_ROOT / path
    return hashlib.md5(file.read_bytes(), usedforsecurity=False).hexdigest()[:10] if file.is_file() else None


def guide_static(path: str) -> str:
    """画像の配信 URL に中身のハッシュを付ける。

    配信側は同じ URL の画像を数時間キャッシュするので、撮り直した画像が古いまま出ないよう URL ごと変える。
    """
    url = static(f'guide/{path}')
    digest = content_digest(path)
    return f'{url}?v={digest}' if digest else url


def mobile_variant(image_path: str) -> str | None:
    """images/x/page-01.webp に対する images/x/page-01-sp.webp が実在すればそのパスを返す。"""
    path = PurePosixPath(image_path)
    if path.stem.endswith('-sp'):
        return None
    candidate = path.with_name(f'{path.stem}-sp{path.suffix}')
    return candidate.as_posix() if (STATIC_GUIDE_ROOT / candidate).is_file() else None


def image_dimensions(image_path: str) -> tuple[int, int] | None:
    """画像を開けない場合は寸法の指定を省く。撮り直しで寸法が変わっても追従するよう更新時刻とサイズを鍵に含める。"""
    try:
        stat = (STATIC_GUIDE_ROOT / image_path).stat()
    except OSError:
        return None
    return _image_dimensions(image_path, (stat.st_mtime_ns, stat.st_size))


@lru_cache(maxsize=2048)
def _image_dimensions(image_path: str, signature: tuple[int, int]) -> tuple[int, int] | None:
    try:
        with Image.open(STATIC_GUIDE_ROOT / image_path) as image:
            return image.size
    except (OSError, ValueError):
        return None


def render_guide_html(html: str, page_path: str) -> str:
    """bleach 後に実行する。入力 HTML 自体のサニタイズは呼び出し側の責務。"""
    soup = BeautifulSoup(html, 'html.parser')
    used_ids = set()
    for heading in soup.find_all(['h2', 'h3', 'h4']):
        slug = re.sub(r'[^\w-]', '', re.sub(r'\s+', '-', heading.get_text().strip())) or 'section'
        anchor = slug
        suffix = 2
        while anchor in used_ids:
            anchor = f'{slug}-{suffix}'
            suffix += 1
        heading['id'] = anchor
        used_ids.add(anchor)
    for table in soup.find_all('table'):
        table.wrap(soup.new_tag('div', attrs={'class': 'guide-table-wrap'}))
    for link in soup.find_all('a', href=True):
        link['href'] = guide_link(str(link['href']), page_path)
    first_image = True
    for img in soup.find_all('img', src=True):
        src = str(img['src'])
        url = urlsplit(src)
        if url.scheme or url.netloc or not url.path.startswith('images/'):
            continue
        image_path = posixpath.normpath(unquote(url.path))
        if not image_path.startswith('images/') or '\\' in image_path:
            continue
        image_url = guide_static(image_path)
        img['src'] = image_url
        img['loading'] = 'eager' if first_image else 'lazy'
        if first_image:
            img['fetchpriority'] = 'high'
            first_image = False
        dimensions = image_dimensions(image_path)
        if dimensions:
            img['width'], img['height'] = dimensions
        img['class'] = ' '.join(dict.fromkeys([*img.get_attribute_list('class'), 'guide-screenshot']))
        target = img
        mobile_path = mobile_variant(image_path)
        if mobile_path:
            # 本文には PC 版だけを書き、スマホ幅では同じ手順の -sp 版を出す（同じ画面を 2 枚並べない）。
            picture = soup.new_tag('picture')
            source = soup.new_tag('source', media=MOBILE_MEDIA, srcset=guide_static(mobile_path))
            mobile_dimensions = image_dimensions(mobile_path)
            if mobile_dimensions:
                source['width'], source['height'] = mobile_dimensions
            picture.append(source)
            img.wrap(picture)
            picture.append(img.extract())
            target = picture
        if not target.find_parent('a'):
            link = soup.new_tag('a', href=image_url, target='_blank', rel='noopener',
                                attrs={'class': 'guide-screenshot-link'})
            target.wrap(link)
    return str(soup)


@lru_cache(maxsize=256)
def page_table_of_contents(html: str) -> list[dict]:
    """描画済みの h2/h3 から入れ子のページ内目次を作る。"""
    headings = BeautifulSoup(html, 'html.parser').find_all(['h2', 'h3'], id=True)
    if len(headings) < 2:
        return []
    items = []
    current_section = None
    for heading in headings:
        item = {'title': heading.get_text(), 'id': heading['id'], 'children': []}
        if heading.name == 'h3' and current_section is not None:
            current_section['children'].append(item)
        else:
            items.append(item)
        if heading.name == 'h2':
            current_section = item
    return items
