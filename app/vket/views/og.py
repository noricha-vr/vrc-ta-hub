"""認証 Cookie を使わず、公開可能なタイトルだけで画像を返す。"""
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_GET

from website.constants import DEFAULT_NEWS_IMAGE_URL
from ..models import VketCollaboration
from ..og import logger, notice_base, render_notice_png, signed_notice


@require_GET
def notice_og(request, pk: int, notice_id: int | None = None):
    if notice_id is None:
        collaboration = VketCollaboration.objects.exclude(
            phase=VketCollaboration.Phase.DRAFT,
        ).filter(pk=pk).first()
        if collaboration is None:
            return redirect(DEFAULT_NEWS_IMAGE_URL)
        title = f'{collaboration.name}のお知らせ'
    else:
        collaboration = get_object_or_404(
            VketCollaboration.objects.exclude(phase=VketCollaboration.Phase.DRAFT), pk=pk,
        )
        notice = signed_notice(collaboration, notice_id, request.GET.get('s'))
        if notice is None:
            raise Http404
        title = notice.title
    base = notice_base(collaboration)
    if base is None:
        return redirect(DEFAULT_NEWS_IMAGE_URL)
    try:
        content = render_notice_png(base, title)
    except Exception:
        # 例外文にも自由入力が入り得るため、本文・タイトル・署名・例外内容は出さない。
        logger.warning('vket_og_render_failed collaboration_id=%s notice_id=%s', pk, notice_id)
        return redirect(DEFAULT_NEWS_IMAGE_URL)
    response = HttpResponse(content, content_type='image/png')
    response['Cache-Control'] = 'public, max-age=86400'
    return response
