"""nginx の host 正規化設定テスト。"""

from pathlib import Path

from django.test import SimpleTestCase

from website.middleware import _build_cloud_run_preview_host_pattern_source


REPO_ROOT = Path(__file__).resolve().parents[3]


class NginxConfigTest(SimpleTestCase):
    def setUp(self):
        self.nginx_config = (REPO_ROOT / 'nginx-app.conf').read_text()

    def test_cloud_run_preview_host_is_rewritten_before_proxy(self):
        expected_pattern = _build_cloud_run_preview_host_pattern_source()

        self.assertIn('map $http_host $django_upstream_host {', self.nginx_config)
        self.assertIn(
            f'~{expected_pattern} vrc-ta-hub.com;',
            self.nginx_config,
        )
        self.assertIn('proxy_set_header Host $django_upstream_host;', self.nginx_config)

    def test_nginx_rule_covers_dev_service_preview_host(self):
        self.assertIn('vrc\\-ta\\-hub\\-dev', self.nginx_config)

    def test_nginx_does_not_forward_raw_cloud_run_host(self):
        self.assertNotIn('proxy_set_header Host $http_host;', self.nginx_config)
