"""HTMLサニタイズ・iframeホワイトリスト・タグエスケープを提供するモジュール."""
from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from website.constants import is_site_domain

logger = logging.getLogger(__name__)

# iframeのsrc属性で許可するドメインのホワイトリスト
ALLOWED_IFRAME_DOMAINS = frozenset([
    # 動画
    'www.youtube.com',
    'www.youtube-nocookie.com',
    'player.vimeo.com',
    # スライド共有
    'docs.google.com',
    'www.slideshare.net',
    'speakerdeck.com',
    'www.canva.com',
    'prezi.com',
    'pitch.com',
    'www.figma.com',
    'onedrive.live.com',
    'view.officeapps.live.com',
])


def _filter_iframe_attributes(tag: str, name: str, value: str) -> bool:
    """iframeの属性をフィルタリングする

    src属性は信頼できるドメインのみ許可し、
    それ以外の属性は許可リストで判定する。

    Args:
        tag: タグ名（常に'iframe'）
        name: 属性名
        value: 属性値

    Returns:
        属性を許可する場合はTrue、除去する場合はFalse
    """
    if name == 'src':
        try:
            parsed = urlparse(value)
            # スキームがhttpまたはhttpsのみ許可（data:, javascript:等は除外）
            if parsed.scheme not in ('http', 'https'):
                return False
            # 自ドメイン配下（サブドメイン含む）はiframeでの埋め込みを禁止（アップロード偽装等の踏み台になり得る）
            if is_site_domain(parsed.hostname):
                return False
            return parsed.netloc in ALLOWED_IFRAME_DOMAINS
        except (TypeError, ValueError):
            logger.exception("iframe srcのURL解析に失敗しました: src=%r", value)
            return False
    # src以外の属性は許可リストで判定
    return name in ('frameborder', 'allowfullscreen', 'width', 'height',
                    'referrerpolicy', 'allow')


def _escape_unknown_html_tags(text: str) -> str:
    """Markdown変換前に、許可されていないHTMLタグ形式の文字列をエスケープする

    P-AMI<Q>のような技術用語内の<>がHTMLタグとして解釈されるのを防ぐ。
    コードブロックやインラインコード内のタグは保護される。

    Args:
        text: 入力テキスト（Markdown形式）

    Returns:
        エスケープ処理されたテキスト
    """
    # 許可されたHTMLタグのリスト（convert_markdownのallowed_tagsと同期）
    allowed_tags = {
        'a', 'p', 'h1', 'h2', 'h3', 'h4', 'ul', 'ol', 'li', 'strong', 'em',
        'code', 'pre', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'hr',
        'br', 'blockquote', 'div', 'iframe', 'span', 'img', 'button', 'i'
    }

    # コードブロックを一時的に保護（```...```）
    code_blocks = []

    def protect_code_block(match):
        code_blocks.append(match.group(0))
        return f'\x00CODE_BLOCK_{len(code_blocks) - 1}\x00'

    text = re.sub(r'```[\s\S]*?```', protect_code_block, text)

    # インラインコードを一時的に保護（`...`）
    inline_codes = []

    def protect_inline_code(match):
        inline_codes.append(match.group(0))
        return f'\x00INLINE_CODE_{len(inline_codes) - 1}\x00'

    text = re.sub(r'`[^`]+`', protect_inline_code, text)

    # HTMLタグパターンをエスケープ（許可タグ以外）
    def escape_tag(match):
        full_match = match.group(0)
        tag_name = match.group(1).lower()

        if tag_name in allowed_tags:
            return full_match
        return full_match.replace('<', '&lt;').replace('>', '&gt;')

    # 開始タグ・自己閉じタグ: <tagname...>
    text = re.sub(r'<([a-zA-Z][a-zA-Z0-9]*)[^>]*/?>', escape_tag, text)

    # 閉じタグ: </tagname>
    def escape_closing_tag(match):
        full_match = match.group(0)
        tag_name = match.group(1).lower()

        if tag_name in allowed_tags:
            return full_match
        return full_match.replace('<', '&lt;').replace('>', '&gt;')

    text = re.sub(r'</([a-zA-Z][a-zA-Z0-9]*)>', escape_closing_tag, text)

    # 保護したコードを復元
    for i, block in enumerate(code_blocks):
        text = text.replace(f'\x00CODE_BLOCK_{i}\x00', block)

    for i, code in enumerate(inline_codes):
        text = text.replace(f'\x00INLINE_CODE_{i}\x00', code)

    return text
