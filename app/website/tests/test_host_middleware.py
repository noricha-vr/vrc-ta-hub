"""Cloud Run host 正規化 middleware の回帰テスト。"""

from asgiref.sync import async_to_sync
from django.core.exceptions import DisallowedHost
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from website.asgi import CloudRunHostCanonicalizingASGIApplication
from website.middleware import CanonicalCloudRunHostMiddleware
from website.wsgi import CloudRunHostCanonicalizingWSGIApplication


class CanonicalCloudRunHostMiddlewareTest(SimpleTestCase):
    def setUp(self):
        self.request_factory = RequestFactory()

    @override_settings(
        ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1', 'vrc-ta-hub.com'],
    )
    def test_cloud_run_revision_host_reported_by_disallowed_host_is_canonicalized(self):
        calls = []

        def get_response(request):
            calls.append(request.get_host())
            if len(calls) == 1:
                raise DisallowedHost(
                    "Invalid HTTP_HOST header: 'rev-24d1224---vrc-ta-hub-mhbhtr6sha-an.a.run.app'. "
                    "You may need to add 'rev-24d1224---vrc-ta-hub-mhbhtr6sha-an.a.run.app' to ALLOWED_HOSTS."
                )
            return HttpResponse(request.get_host())

        request = self.request_factory.get(
            '/healthz/',
            HTTP_HOST='rev-24d1224---vrc-ta-hub-mhbhtr6sha-an.a.run.app',
        )

        response = CanonicalCloudRunHostMiddleware(get_response)(request)

        self.assertEqual(calls, ['vrc-ta-hub.com', 'vrc-ta-hub.com'])
        self.assertEqual(response.content.decode(), 'vrc-ta-hub.com')

    @override_settings(
        ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1', 'vrc-ta-hub.com'],
    )
    def test_disallowed_host_fallback_rejects_other_cloud_run_service(self):
        def get_response(_request):
            raise DisallowedHost(
                "Invalid HTTP_HOST header: 'rev-24d1224---other-service-mhbhtr6sha-an.a.run.app'. "
                "You may need to add 'rev-24d1224---other-service-mhbhtr6sha-an.a.run.app' to ALLOWED_HOSTS."
            )

        request = self.request_factory.get(
            '/healthz/',
            HTTP_HOST='rev-24d1224---other-service-mhbhtr6sha-an.a.run.app',
        )

        with self.assertRaises(DisallowedHost):
            CanonicalCloudRunHostMiddleware(get_response)(request)

    @override_settings(
        ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1', 'vrc-ta-hub.com'],
    )
    def test_disallowed_host_fallback_does_not_retry_after_url_resolution(self):
        calls = []

        def get_response(_request):
            calls.append('called')
            raise DisallowedHost(
                "Invalid HTTP_HOST header: 'rev-24d1224---vrc-ta-hub-mhbhtr6sha-an.a.run.app'. "
                "You may need to add 'rev-24d1224---vrc-ta-hub-mhbhtr6sha-an.a.run.app' to ALLOWED_HOSTS."
            )

        request = self.request_factory.get(
            '/healthz/',
            HTTP_HOST='rev-24d1224---vrc-ta-hub-mhbhtr6sha-an.a.run.app',
        )
        request.resolver_match = object()

        with self.assertRaises(DisallowedHost):
            CanonicalCloudRunHostMiddleware(get_response)(request)

        self.assertEqual(calls, ['called'])


class CloudRunHostCanonicalizingWSGIApplicationTest(SimpleTestCase):
    def test_cloud_run_revision_host_is_canonicalized_before_django_request(self):
        captured_environ = {}

        def django_application(environ, start_response):
            captured_environ.update(environ)
            start_response('200 OK', [])
            return [b'ok']

        environ = {
            'HTTP_HOST': 'rev-24d1224---vrc-ta-hub-mhbhtr6sha-an.a.run.app',
            'HTTP_X_FORWARDED_HOST': 'rev-24d1224---vrc-ta-hub-mhbhtr6sha-an.a.run.app',
            'SERVER_NAME': 'rev-24d1224---vrc-ta-hub-mhbhtr6sha-an.a.run.app',
        }

        response = CloudRunHostCanonicalizingWSGIApplication(django_application)(
            environ,
            lambda _status, _headers: None,
        )

        self.assertEqual(list(response), [b'ok'])
        self.assertEqual(captured_environ['HTTP_HOST'], 'vrc-ta-hub.com')
        self.assertEqual(captured_environ['HTTP_X_FORWARDED_HOST'], 'vrc-ta-hub.com')
        self.assertEqual(captured_environ['SERVER_NAME'], 'vrc-ta-hub.com')

    def test_other_cloud_run_service_host_is_left_for_django_to_reject(self):
        captured_environ = {}

        def django_application(environ, start_response):
            captured_environ.update(environ)
            start_response('200 OK', [])
            return [b'ok']

        environ = {
            'HTTP_HOST': 'rev-24d1224---other-service-mhbhtr6sha-an.a.run.app',
            'SERVER_NAME': 'rev-24d1224---other-service-mhbhtr6sha-an.a.run.app',
        }

        CloudRunHostCanonicalizingWSGIApplication(django_application)(
            environ,
            lambda _status, _headers: None,
        )

        self.assertEqual(
            captured_environ['HTTP_HOST'],
            'rev-24d1224---other-service-mhbhtr6sha-an.a.run.app',
        )
        self.assertEqual(
            captured_environ['SERVER_NAME'],
            'rev-24d1224---other-service-mhbhtr6sha-an.a.run.app',
        )


class CloudRunHostCanonicalizingASGIApplicationTest(SimpleTestCase):
    def test_cloud_run_revision_host_is_canonicalized_before_django_request(self):
        captured_scope = {}

        async def django_application(scope, _receive, _send):
            captured_scope.update(scope)

        scope = {
            'type': 'http',
            'headers': [
                (b'host', b'rev-24d1224---vrc-ta-hub-mhbhtr6sha-an.a.run.app'),
                (
                    b'x-forwarded-host',
                    b'rev-24d1224---vrc-ta-hub-mhbhtr6sha-an.a.run.app',
                ),
            ],
            'server': ('rev-24d1224---vrc-ta-hub-mhbhtr6sha-an.a.run.app', 443),
        }

        async_to_sync(CloudRunHostCanonicalizingASGIApplication(django_application))(
            scope,
            lambda: None,
            lambda _message: None,
        )

        self.assertEqual(
            captured_scope['headers'],
            [
                (b'host', b'vrc-ta-hub.com'),
                (b'x-forwarded-host', b'vrc-ta-hub.com'),
            ],
        )
        self.assertEqual(captured_scope['server'], ('vrc-ta-hub.com', 443))

    def test_other_cloud_run_service_host_is_left_for_django_to_reject(self):
        captured_scope = {}

        async def django_application(scope, _receive, _send):
            captured_scope.update(scope)

        scope = {
            'type': 'http',
            'headers': [
                (b'host', b'rev-24d1224---other-service-mhbhtr6sha-an.a.run.app'),
            ],
            'server': ('rev-24d1224---other-service-mhbhtr6sha-an.a.run.app', 443),
        }

        async_to_sync(CloudRunHostCanonicalizingASGIApplication(django_application))(
            scope,
            lambda: None,
            lambda _message: None,
        )

        self.assertEqual(
            captured_scope['headers'],
            [(b'host', b'rev-24d1224---other-service-mhbhtr6sha-an.a.run.app')],
        )
        self.assertEqual(
            captured_scope['server'],
            ('rev-24d1224---other-service-mhbhtr6sha-an.a.run.app', 443),
        )
