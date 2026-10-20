"""参加ページの GET だけを匿名向けの公開シェルにする。"""
from urllib.parse import urlencode

from django.conf import settings
from django.shortcuts import render
from django.utils.cache import add_never_cache_headers, patch_vary_headers

from ..models import VketCollaboration
from ..og import page_metadata


class PublicParticipationShellMixin:
    og_page = ''

    def dispatch(self, request, *args, **kwargs):
        if request.method == 'GET' and not request.user.is_authenticated:
            # ログイン後の表示と同じく、下書きとアーカイブ済みは案内しない（ログインした先が 404 で行き止まりになるため）。
            collaboration = VketCollaboration.objects.exclude(
                phase__in=[VketCollaboration.Phase.DRAFT, VketCollaboration.Phase.ARCHIVED],
            ).filter(pk=kwargs['pk']).first()
            if collaboration is not None:
                metadata = page_metadata(request, collaboration, self.og_page)
                response = render(request, 'vket/page_public.html', {
                    **metadata,
                    'public_title': metadata['og_title'],
                    'public_message': 'ログインしてご確認ください。',
                    'login_label': f'ログインして{metadata["og_title"].split()[-1]}を見る',
                    'login_url': f'{settings.LOGIN_URL}?{urlencode({"next": request.get_full_path()})}',
                })
                add_never_cache_headers(response)
                patch_vary_headers(response, ['Cookie'])
                response['X-Robots-Tag'] = 'noindex'
                return response
        return super().dispatch(request, *args, **kwargs)
