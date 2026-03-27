"""Cloud Run のホスト許可設定テスト"""

import os

from django.http import HttpResponse
from django.test import SimpleTestCase, override_settings
from django.urls import path

from website import settings as website_settings


def ok_view(_request):
    return HttpResponse('ok')


urlpatterns = [
    path('healthz/', ok_view),
]


class AllowedHostsSettingsTest(SimpleTestCase):
    def test_build_allowed_hosts_includes_cloud_run_and_env_hosts(self):
        original_allowed_hosts = os.environ.get('ALLOWED_HOSTS')
        original_http_host = os.environ.get('HTTP_HOST')

        try:
            os.environ['ALLOWED_HOSTS'] = 'example.com, api.example.com'
            os.environ['HTTP_HOST'] = 'preview.example.com'

            allowed_hosts = website_settings._build_allowed_hosts()

            self.assertIn('.a.run.app', website_settings.ALLOWED_HOSTS)
            self.assertIn('.a.run.app', allowed_hosts)
            self.assertIn('example.com', allowed_hosts)
            self.assertIn('api.example.com', allowed_hosts)
            self.assertIn('preview.example.com', allowed_hosts)
        finally:
            if original_allowed_hosts is None:
                os.environ.pop('ALLOWED_HOSTS', None)
            else:
                os.environ['ALLOWED_HOSTS'] = original_allowed_hosts

            if original_http_host is None:
                os.environ.pop('HTTP_HOST', None)
            else:
                os.environ['HTTP_HOST'] = original_http_host

    @override_settings(
        ROOT_URLCONF=__name__,
        ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1', '.a.run.app'],
    )
    def test_cloud_run_revision_host_is_accepted(self):
        response = self.client.get(
            '/healthz/',
            HTTP_HOST='rev-24d1224---vrc-ta-hub-mhbhtr6sha-an.a.run.app',
        )

        self.assertEqual(response.status_code, 200)
