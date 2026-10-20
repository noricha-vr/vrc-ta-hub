"""Hub の公開情報だけからサムネイルの URL と表示文を作る。"""
from hashlib import sha256
from urllib.parse import urlencode

from django.urls import reverse

from website.constants import DEFAULT_NEWS_IMAGE_URL


def image_url(request, route, text, **kwargs):
    version = sha256(text.encode('utf-8')).hexdigest()[:8]
    return request.build_absolute_uri(f'{reverse(route, kwargs=kwargs)}?{urlencode({"v": version})}')


def accepts_public_application(community):
    return community.status == 'approved' and community.accepts_lt_application


def application_text(community):
    return f'{community.name}で発表しませんか？'


def application_metadata(request, community):
    return {
        'og_title': f'{community.name} LT 発表申込み',
        'og_image_url': (
            image_url(request, 'ta_hub:og_lt_apply', application_text(community), pk=community.pk)
            if accepts_public_application(community) else DEFAULT_NEWS_IMAGE_URL
        ),
        'og_url_path': request.path,
        'meta_description': f'{community.name}での発表を申し込めます。ログインしてご確認ください。',
    }
