"""ホストヘッダを補正するミドルウェア。"""

import os
import re

from website.hosts import get_canonical_host, normalize_host


DEFAULT_CLOUD_RUN_SERVICE_NAMES = (
    'vrc-ta-hub',
    'vrc-ta-hub-dev',
)


def _build_cloud_run_service_names() -> tuple[str, ...]:
    configured_names = [
        service_name.strip()
        for service_name in os.environ.get('CLOUD_RUN_SERVICE_NAMES', '').split(',')
        if service_name.strip()
    ]
    service_names = [*configured_names, *DEFAULT_CLOUD_RUN_SERVICE_NAMES]
    return tuple(dict.fromkeys(service_names))


def _build_cloud_run_preview_host_pattern_source() -> str:
    service_names = '|'.join(
        re.escape(service_name)
        for service_name in _build_cloud_run_service_names()
    )
    return (
        rf'^(?:[a-z0-9-]+---)?(?:{service_names})-[a-z0-9]+-[a-z0-9]+'
        rf'\.a\.run\.app(?::\d+)?$'
    )


def _build_cloud_run_preview_host_pattern() -> re.Pattern[str]:
    return re.compile(
        _build_cloud_run_preview_host_pattern_source(),
        re.IGNORECASE,
    )


class CanonicalCloudRunHostMiddleware:
    """Cloud Run のプレビューURLを正規ホストへ寄せる。"""

    def __init__(self, get_response):
        self.get_response = get_response
        self.canonical_host = get_canonical_host()
        self.cloud_run_preview_host_pattern = _build_cloud_run_preview_host_pattern()

    def __call__(self, request):
        host_meta_keys = ('HTTP_HOST', 'HTTP_X_FORWARDED_HOST', 'SERVER_NAME')
        normalized_hosts = [
            normalize_host(request.META.get(meta_key, ''))
            for meta_key in host_meta_keys
        ]
        if any(
            self.cloud_run_preview_host_pattern.match(raw_host)
            for raw_host in normalized_hosts
        ):
            for meta_key in host_meta_keys:
                if request.META.get(meta_key):
                    request.META[meta_key] = self.canonical_host
            request.META['SERVER_NAME'] = self.canonical_host

        return self.get_response(request)
