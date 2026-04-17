"""Cloud Run のホスト許可設定テスト"""

import os

from django.core.exceptions import DisallowedHost
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import path

from website.middleware import (
    CanonicalCloudRunHostMiddleware,
    DEFAULT_CLOUD_RUN_SERVICE_NAMES,
    _build_cloud_run_preview_host_pattern,
    _build_cloud_run_service_names,
)
from website import settings as website_settings


def ok_view(request):
    return HttpResponse(request.get_host())


urlpatterns = [
    path('healthz/', ok_view),
]


class AllowedHostsSettingsTest(SimpleTestCase):
    def setUp(self):
        self.request_factory = RequestFactory()

    def test_build_cloud_run_service_names_includes_default_services(self):
        original_service_names = os.environ.get('CLOUD_RUN_SERVICE_NAMES')

        try:
            os.environ['CLOUD_RUN_SERVICE_NAMES'] = 'preview-service, vrc-ta-hub'

            self.assertEqual(
                _build_cloud_run_service_names(),
                ('preview-service', *DEFAULT_CLOUD_RUN_SERVICE_NAMES),
            )
        finally:
            if original_service_names is None:
                os.environ.pop('CLOUD_RUN_SERVICE_NAMES', None)
            else:
                os.environ['CLOUD_RUN_SERVICE_NAMES'] = original_service_names

    def test_build_allowed_hosts_includes_canonical_and_env_hosts_without_run_app_wildcard(self):
        original_allowed_hosts = os.environ.get('ALLOWED_HOSTS')
        original_app_canonical_host = os.environ.get('APP_CANONICAL_HOST')
        original_http_host = os.environ.get('HTTP_HOST')

        try:
            os.environ['ALLOWED_HOSTS'] = 'example.com, api.example.com'
            os.environ['APP_CANONICAL_HOST'] = 'https://preview.vrc-ta-hub.com:8443/'
            os.environ['HTTP_HOST'] = 'https://preview.example.com:9443/'

            allowed_hosts = website_settings._build_allowed_hosts()

            self.assertNotIn('.a.run.app', website_settings.ALLOWED_HOSTS)
            self.assertIn('preview.vrc-ta-hub.com', allowed_hosts)
            self.assertIn('example.com', allowed_hosts)
            self.assertIn('api.example.com', allowed_hosts)
            self.assertIn('preview.example.com', allowed_hosts)
        finally:
            if original_allowed_hosts is None:
                os.environ.pop('ALLOWED_HOSTS', None)
            else:
                os.environ['ALLOWED_HOSTS'] = original_allowed_hosts

            if original_app_canonical_host is None:
                os.environ.pop('APP_CANONICAL_HOST', None)
            else:
                os.environ['APP_CANONICAL_HOST'] = original_app_canonical_host

            if original_http_host is None:
                os.environ.pop('HTTP_HOST', None)
            else:
                os.environ['HTTP_HOST'] = original_http_host

    def test_cloud_run_preview_host_pattern_matches_supported_services_only(self):
        pattern = _build_cloud_run_preview_host_pattern()

        self.assertRegex(
            'rev-24d1224---vrc-ta-hub-mhbhtr6sha-an.a.run.app',
            pattern,
        )
        self.assertRegex(
            'vrc-ta-hub-mhbhtr6sha-an.a.run.app',
            pattern,
        )
        self.assertRegex(
            'rev-24d1224---vrc-ta-hub-dev-mhbhtr6sha-an.a.run.app',
            pattern,
        )
        self.assertNotRegex(
            'rev-24d1224---other-service-mhbhtr6sha-an.a.run.app',
            pattern,
        )

    @override_settings(
        ROOT_URLCONF=__name__,
        ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1', 'vrc-ta-hub.com'],
        MIDDLEWARE=[
            'website.middleware.CanonicalCloudRunHostMiddleware',
            'django.middleware.common.CommonMiddleware',
        ],
    )
    def test_cloud_run_revision_host_is_canonicalized(self):
        response = self.client.get(
            '/healthz/',
            HTTP_HOST='rev-24d1224---vrc-ta-hub-mhbhtr6sha-an.a.run.app',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), 'vrc-ta-hub.com')

    @override_settings(
        ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1', 'vrc-ta-hub.com'],
    )
    def test_other_service_cloud_run_host_is_rejected(self):
        request = self.request_factory.get(
            '/healthz/',
            HTTP_HOST='rev-24d1224---other-service-mhbhtr6sha-an.a.run.app',
        )
        CanonicalCloudRunHostMiddleware(lambda _: HttpResponse('ok'))(request)

        with self.assertRaises(DisallowedHost):
            request.get_host()

    @override_settings(
        ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1', 'vrc-ta-hub.com'],
    )
    def test_similar_prefix_service_cloud_run_host_is_rejected(self):
        request = self.request_factory.get(
            '/healthz/',
            HTTP_HOST='rev-24d1224---other-vrc-ta-hub-mhbhtr6sha-an.a.run.app',
        )
        CanonicalCloudRunHostMiddleware(lambda _: HttpResponse('ok'))(request)

        with self.assertRaises(DisallowedHost):
            request.get_host()

    @override_settings(
        ROOT_URLCONF=__name__,
        ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1', 'vrc-ta-hub.com'],
        MIDDLEWARE=[
            'website.middleware.CanonicalCloudRunHostMiddleware',
            'django.middleware.common.CommonMiddleware',
        ],
    )
    def test_dev_service_cloud_run_host_is_canonicalized(self):
        response = self.client.get(
            '/healthz/',
            HTTP_HOST='rev-24d1224---vrc-ta-hub-dev-mhbhtr6sha-an.a.run.app',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), 'vrc-ta-hub.com')

    @override_settings(
        ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1', 'vrc-ta-hub.com'],
    )
    def test_server_name_is_canonicalized_when_http_host_is_absent(self):
        request = self.request_factory.get('/healthz/')
        request.META.pop('HTTP_HOST', None)
        request.META['SERVER_NAME'] = 'rev-24d1224---vrc-ta-hub-mhbhtr6sha-an.a.run.app'

        CanonicalCloudRunHostMiddleware(lambda req: HttpResponse(req.get_host()))(request)

        self.assertEqual(request.get_host(), 'vrc-ta-hub.com')

    @override_settings(
        ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1', 'vrc-ta-hub.com'],
    )
    def test_url_form_forwarded_host_is_canonicalized(self):
        request = self.request_factory.get(
            '/healthz/',
            HTTP_X_FORWARDED_HOST='https://rev-24d1224---vrc-ta-hub-mhbhtr6sha-an.a.run.app:443/',
        )
        request.META.pop('HTTP_HOST', None)
        request.META['SERVER_NAME'] = 'https://rev-24d1224---vrc-ta-hub-mhbhtr6sha-an.a.run.app:443/'

        response = CanonicalCloudRunHostMiddleware(
            lambda req: HttpResponse(req.get_host())
        )(request)

        self.assertEqual(response.content.decode(), 'vrc-ta-hub.com')

    @override_settings(
        ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1', 'vrc-ta-hub.com'],
    )
    def test_invalid_forwarded_host_does_not_crash_preview_host_detection(self):
        request = self.request_factory.get(
            '/healthz/',
            HTTP_X_FORWARDED_HOST='[::1',
        )
        request.META.pop('HTTP_HOST', None)
        request.META['SERVER_NAME'] = '[::1'

        CanonicalCloudRunHostMiddleware(lambda req: HttpResponse('ok'))(request)

        with self.assertRaises(DisallowedHost):
            request.get_host()

    @override_settings(
        ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1', 'preview.vrc-ta-hub.com'],
    )
    def test_cloud_run_revision_host_uses_normalized_canonical_host(self):
        original_app_canonical_host = os.environ.get('APP_CANONICAL_HOST')

        try:
            os.environ['APP_CANONICAL_HOST'] = 'https://preview.vrc-ta-hub.com/'
            request = self.request_factory.get(
                '/healthz/',
                HTTP_HOST='rev-24d1224---vrc-ta-hub-mhbhtr6sha-an.a.run.app',
            )

            response = CanonicalCloudRunHostMiddleware(
                lambda req: HttpResponse(req.get_host())
            )(request)

            self.assertEqual(response.content.decode(), 'preview.vrc-ta-hub.com')
        finally:
            if original_app_canonical_host is None:
                os.environ.pop('APP_CANONICAL_HOST', None)
            else:
                os.environ['APP_CANONICAL_HOST'] = original_app_canonical_host

    def test_host_canonicalization_middleware_runs_before_cors(self):
        middleware = website_settings.MIDDLEWARE

        self.assertLess(
            middleware.index('website.middleware.CanonicalCloudRunHostMiddleware'),
            middleware.index('corsheaders.middleware.CorsMiddleware'),
        )
