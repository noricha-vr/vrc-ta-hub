"""ホストヘッダを補正するミドルウェア。"""

import os
import re


def _build_cloud_run_preview_host_pattern() -> re.Pattern[str]:
    service_name = re.escape(os.environ.get('K_SERVICE', 'vrc-ta-hub'))
    return re.compile(
        rf'^(?:[a-z0-9-]+---)?{service_name}-[a-z0-9]+-[a-z0-9]+\.a\.run\.app(?::\d+)?$',
        re.IGNORECASE,
    )


class CanonicalCloudRunHostMiddleware:
    """Cloud Run のプレビューURLを正規ホストへ寄せる。"""

    def __init__(self, get_response):
        self.get_response = get_response
        self.canonical_host = os.environ.get('APP_CANONICAL_HOST', 'vrc-ta-hub.com')
        self.cloud_run_preview_host_pattern = _build_cloud_run_preview_host_pattern()

    def __call__(self, request):
        raw_host = request.META.get('HTTP_HOST', '')
        if self.cloud_run_preview_host_pattern.match(raw_host):
            request.META['HTTP_HOST'] = self.canonical_host
            request.META['SERVER_NAME'] = self.canonical_host

        return self.get_response(request)
