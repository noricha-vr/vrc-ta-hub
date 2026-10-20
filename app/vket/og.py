"""コラボの OGP 設定・署名付きリンク・お知らせ画像の描画。"""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from urllib.parse import urlencode

from django.core import signing
from django.urls import reverse

from ta_hub.og_image import absolute_static_url, render_text_png
from website.constants import DEFAULT_NEWS_IMAGE_URL
from .models import VketNotice

logger = logging.getLogger(__name__)
ASSET_ROOT = Path(__file__).resolve().parent / 'og_assets'
SIGNING_SALT = 'vket-notice-og'


def _settings(collaboration) -> dict:
    value = collaboration.settings_json
    return value if isinstance(value, dict) else {}


def _images(collaboration) -> dict:
    value = _settings(collaboration).get('og_images')
    return value if isinstance(value, dict) else {}


def page_metadata(request, collaboration, page: str) -> dict:
    label = {'apply': '参加登録', 'status': '参加状況', 'stage_register': 'ステージ登録', 'detail': ''}[page]
    image = _images(collaboration).get(page)
    image_url = DEFAULT_NEWS_IMAGE_URL
    if isinstance(image, str):
        image = image.strip()
        if (image and not re.match(r'[A-Za-z][A-Za-z0-9+.-]*:', image)
                and not any(c in image for c in '?#') and not image.startswith('/')
                and '\\' not in image and '..' not in image and not any(ord(c) < 32 for c in image)):
            image_url = absolute_static_url(request, image)
    return {
        'og_title': f'{collaboration.name} {label}'.rstrip(),
        'og_image_url': image_url,
        'og_url_path': request.path,
        'meta_description': (
            f'{collaboration.name}の開催情報と確定したスケジュールを確認できます。'
            if page == 'detail' else f'{collaboration.name}の{label}ページです。ログインしてご確認ください。'
        ),
    }


def notice_base(collaboration) -> Path | None:
    name = _images(collaboration).get('notice_base')
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', name):
        return None
    path = (ASSET_ROOT / name).resolve()
    if not path.is_relative_to(ASSET_ROOT.resolve()) or not path.is_dir():
        return None
    return path


def sign_notice(notice_id: int) -> str:
    return signing.Signer(salt=SIGNING_SALT).sign(str(notice_id))


def valid_notice_signature(notice_id, signature) -> bool:
    # DB の整数範囲外や巨大な入力も query 前に拒否する。
    if not isinstance(signature, str) or len(signature) > 128:
        return False
    value = str(notice_id)
    if not value.isascii() or not value.isdecimal() or len(value) > 19:
        return False
    if not 0 < int(value) <= 9223372036854775807:
        return False
    try:
        return signing.Signer(salt=SIGNING_SALT).unsign(signature) == value
    except signing.BadSignature:
        return False


def signed_notice(collaboration, notice_id, signature):
    """署名のないリクエストでは Notice を含む私的なテーブルに触れない。"""
    if not valid_notice_signature(notice_id, signature):
        return None
    # お知らせに下書きは無く、署名付きリンクは配信時にだけ作る。本文・受信者・参加情報は取得しない。
    return VketNotice.objects.filter(
        pk=int(notice_id), collaboration=collaboration,
    ).only('id', 'collaboration_id', 'title').first()


def notice_link(request, notice) -> str:
    path = reverse('vket:notice_list', kwargs={'pk': notice.collaboration_id})
    query = urlencode({'open': notice.pk, 's': sign_notice(notice.pk)})
    return request.build_absolute_uri(f'{path}?{query}')


def notice_image_url(request, collaboration, title: str, notice=None) -> str:
    kwargs = {'pk': collaboration.pk}
    query = {}
    route = 'vket:notice_og_list'
    if notice is not None:
        route = 'vket:notice_og_detail'
        kwargs['notice_id'] = notice.pk
        query['s'] = sign_notice(notice.pk)
    query['v'] = hashlib.sha256(title.encode('utf-8')).hexdigest()[:8]
    return request.build_absolute_uri(f'{reverse(route, kwargs=kwargs)}?{urlencode(query)}')


def notice_metadata(request, collaboration) -> dict:
    title = f'{collaboration.name} お知らせ'
    notice = signed_notice(collaboration, request.GET.get('open'), request.GET.get('s'))
    if notice is not None:
        title = notice.title
    image_url = DEFAULT_NEWS_IMAGE_URL
    # 背景素材が無いコラボは、署名付きリンクでも従来の画像設定を使う（画像側が共通画像へ転送してしまうため）。
    if notice_base(collaboration):
        image_url = notice_image_url(
            request, collaboration, notice.title if notice else f'{collaboration.name}のお知らせ', notice,
        )
    else:
        # 従来の静的パスと絶対 URL の設定も引き続き利用できる。
        legacy = _settings(collaboration).get('notice_og_image')
        if isinstance(legacy, str) and legacy.strip():
            legacy = legacy.strip()
            image_url = legacy if legacy.startswith(('http://', 'https://')) else absolute_static_url(request, legacy)
    # 共有先が og:url で個別のお知らせを区別できるよう、正しい署名付きの時だけ open と s を残す。
    og_url_path = request.path
    if notice is not None:
        og_url_path = f"{request.path}?{urlencode({'open': notice.pk, 's': sign_notice(notice.pk)})}"
    return {
        'og_title': title,
        'og_image_url': image_url,
        'og_url_path': og_url_path,
        'meta_description': (
            f'{collaboration.name}の開催準備や発表に関するお知らせを確認できます。'
            'お知らせの内容を見るにはログインが必要です。'
        ),
    }


def render_notice_png(base: Path, title: str) -> bytes:
    return render_text_png(base, title, 'notice-base')
