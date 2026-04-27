"""ホストヘッダを補正するミドルウェア。"""

import os
import re

from django.core.exceptions import DisallowedHost

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


def _normalize_preview_host_candidate(value: str) -> str:
    try:
        return normalize_host(value)
    except ValueError:
        # 外部入力が壊れていても 500 にはせず、preview host 不一致として弾く。参照: PR #247
        return ''


def _extract_disallowed_host(error: DisallowedHost) -> str:
    match = re.search(r"Invalid HTTP_HOST header: '([^']+)'", str(error))
    if match:
        return match.group(1)

    return ''


class CanonicalCloudRunHostMiddleware:
    """Cloud Run のプレビューURLを正規ホストへ寄せる。"""

    def __init__(self, get_response):
        self.get_response = get_response
        self.canonical_host = get_canonical_host()
        self.cloud_run_preview_host_pattern = _build_cloud_run_preview_host_pattern()

    def _is_supported_preview_host(self, value: str) -> bool:
        normalized_host = _normalize_preview_host_candidate(value)
        return bool(self.cloud_run_preview_host_pattern.match(normalized_host))

    def canonicalize_host_mapping(self, host_mapping: dict[str, str]) -> bool:
        """正規化対象の Cloud Run preview host を canonical host へ寄せる。"""
        host_meta_keys = ('HTTP_HOST', 'HTTP_X_FORWARDED_HOST', 'SERVER_NAME')
        # proxy 差分で absolute URL や host:port が混ざるので、判定前に host へ正規化する。参照: PR #247
        if any(
            self._is_supported_preview_host(host_mapping.get(meta_key, ''))
            for meta_key in host_meta_keys
        ):
            for meta_key in host_meta_keys:
                if host_mapping.get(meta_key):
                    host_mapping[meta_key] = self.canonical_host
            host_mapping['SERVER_NAME'] = self.canonical_host
            return True

        return False

    def __call__(self, request):
        self.canonicalize_host_mapping(request.META)

        try:
            return self.get_response(request)
        except DisallowedHost as error:
            if getattr(request, 'resolver_match', None) is not None:
                raise

            disallowed_host = _extract_disallowed_host(error)
            # 観測ログと同じ Django get_host() 経由の拒否だけを、同じ service 名ホワイトリストで救済する。参照: PR #247
            if not self._is_supported_preview_host(disallowed_host):
                raise

            request.META['HTTP_HOST'] = self.canonical_host
            request.META['SERVER_NAME'] = self.canonical_host
            if request.META.get('HTTP_X_FORWARDED_HOST'):
                request.META['HTTP_X_FORWARDED_HOST'] = self.canonical_host
            return self.get_response(request)
