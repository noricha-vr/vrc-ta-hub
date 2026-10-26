"""サニタイズ後のガイド HTML に配信 URL と画像の表示属性を付ける。"""
import posixpath
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from django.templatetags.static import static

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


def mobile_variant(image_path: str) -> str | None:
    """images/x/page-01.webp に対する images/x/page-01-sp.webp が実在すればそのパスを返す。"""
    path = PurePosixPath(image_path)
    if path.stem.endswith('-sp'):
        return None
    candidate = path.with_name(f'{path.stem}-sp{path.suffix}')
    return candidate.as_posix() if (STATIC_GUIDE_ROOT / candidate).is_file() else None


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
        target = img
        mobile_path = mobile_variant(image_path)
        if mobile_path:
            # 本文には PC 版だけを書き、スマホ幅では同じ手順の -sp 版を出す（同じ画面を 2 枚並べない）。
            picture = soup.new_tag('picture')
            picture.append(soup.new_tag('source', media=MOBILE_MEDIA, srcset=static(f'guide/{mobile_path}')))
            img.wrap(picture)
            picture.append(img.extract())
            target = picture
        if not target.find_parent('a'):
            link = soup.new_tag('a', href=image_url, target='_blank', rel='noopener')
            target.wrap(link)
    return str(soup)
