"""認証・クエリ文字列に依存しない公開サムネイル。"""
import logging
from hashlib import sha256

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_GET

from community.models import Community
from guide.views import load_markdown_content
from website.constants import DEFAULT_NEWS_IMAGE_URL
from .og import application_text
from .og_image import ASSET_ROOT, render_text_png

logger = logging.getLogger(__name__)


def _png(background, text, object_id):
    try:
        content = render_text_png(ASSET_ROOT / background, text)
    except Exception:
        # 自由入力や例外文を出さず、対象の識別子だけを残す。
        logger.warning('hub_og_render_failed object_id=%s', object_id)
        return redirect(DEFAULT_NEWS_IMAGE_URL)
    response = HttpResponse(content, content_type='image/png')
    response['Cache-Control'] = 'public, max-age=86400'
    return response


@require_GET
def lt_apply_og(request, pk):
    community = get_object_or_404(Community, pk=pk, status='approved', accepts_lt_application=True)
    return _png('lt-apply', application_text(community), community.pk)


@require_GET
def community_og(request, pk):
    community = get_object_or_404(Community, pk=pk, status='approved')
    return _png('community', community.name, community.pk)


@require_GET
def guide_og(request, path):
    _, frontmatter = load_markdown_content(path, render_body=False)
    # ログにはパスやタイトルを出さず、検証済みパスの固定長ハッシュだけを使う。
    return _png('guide', frontmatter.get('title', 'ガイド'), sha256(path.encode()).hexdigest()[:8])
