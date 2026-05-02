"""
ASGI config for website project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

from website.middleware import (
    CanonicalCloudRunHostMiddleware,
    install_cloud_run_preview_host_validator,
)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'website.settings')
# 設定ファイルを触らず、Django の Host 検証まで raw revision host が残った経路だけ救済する。
install_cloud_run_preview_host_validator()


class CloudRunHostCanonicalizingASGIApplication:
    """Django request 生成前に Cloud Run preview host を正規化する。"""

    def __init__(self, django_application):
        self.django_application = django_application
        self.host_middleware = CanonicalCloudRunHostMiddleware(lambda request: request)

    async def __call__(self, scope, receive, send):
        if scope.get('type') != 'http':
            await self.django_application(scope, receive, send)
            return

        normalized_scope = self._canonicalize_http_scope(scope)
        await self.django_application(normalized_scope, receive, send)

    def _canonicalize_http_scope(self, scope):
        header_pairs = list(scope.get('headers', ()))
        host_mapping = self._build_host_mapping(scope, header_pairs)

        if not self.host_middleware.canonicalize_host_mapping(host_mapping):
            return scope

        normalized_scope = dict(scope)
        normalized_scope['headers'] = self._replace_host_headers(
            header_pairs,
            host_mapping,
        )
        server = normalized_scope.get('server')
        if server:
            normalized_scope['server'] = (host_mapping['SERVER_NAME'], server[1])

        return normalized_scope

    @staticmethod
    def _build_host_mapping(scope, header_pairs):
        host_mapping = {}
        for raw_name, raw_value in header_pairs:
            name = raw_name.lower()
            if name == b'host':
                host_mapping['HTTP_HOST'] = raw_value.decode('latin1')
            elif name == b'x-forwarded-host':
                host_mapping['HTTP_X_FORWARDED_HOST'] = raw_value.decode('latin1')

        server = scope.get('server')
        if server:
            host_mapping['SERVER_NAME'] = server[0]

        return host_mapping

    @staticmethod
    def _replace_host_headers(header_pairs, host_mapping):
        encoded_http_host = host_mapping.get('HTTP_HOST', '').encode('latin1')
        encoded_forwarded_host = host_mapping.get(
            'HTTP_X_FORWARDED_HOST',
            '',
        ).encode('latin1')

        normalized_headers = []
        for raw_name, raw_value in header_pairs:
            name = raw_name.lower()
            if name == b'host' and encoded_http_host:
                normalized_headers.append((raw_name, encoded_http_host))
            elif name == b'x-forwarded-host' and encoded_forwarded_host:
                normalized_headers.append((raw_name, encoded_forwarded_host))
            else:
                normalized_headers.append((raw_name, raw_value))

        return normalized_headers


application = CloudRunHostCanonicalizingASGIApplication(get_asgi_application())
