from unittest.mock import MagicMock, patch

import requests as requests_lib
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from community.models import Community, CommunityReport
from community.views import _get_client_ip


@override_settings(DISCORD_REPORT_WEBHOOK_URL='')
class CommunityReportViewTest(TestCase):
    def setUp(self):
        cache.clear()
        self.community = Community.objects.create(
            name='テスト集会',
            frequency='毎週',
            status='approved',
        )
        self.url = reverse('community:report', kwargs={'pk': self.community.pk})

    def test_report_success(self):
        """通報成功（POST -> 302 + レコード作成 + IP記録）"""
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CommunityReport.objects.count(), 1)
        report = CommunityReport.objects.first()
        self.assertEqual(report.community, self.community)
        self.assertEqual(report.ip_address, '127.0.0.1')

    def test_duplicate_report_blocked(self):
        """重複通報防止（同一IP連続POST -> 2件目は作成されない）"""
        self.client.post(self.url)
        self.assertEqual(CommunityReport.objects.count(), 1)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CommunityReport.objects.count(), 1)

    def test_unapproved_community_returns_404(self):
        """未承認集会への通報 -> 404"""
        self.community.status = 'pending'
        self.community.save()
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 404)

    def test_get_returns_405(self):
        """GETリクエスト -> 405"""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    @override_settings(DISCORD_REPORT_WEBHOOK_URL='https://discord.com/api/webhooks/test')
    def test_webhook_sent_on_report(self):
        """通報時にDiscord Webhookが送信される"""
        with patch('community.views.requests.post') as mock_post:
            mock_post.return_value = MagicMock(ok=True)
            self.client.post(self.url)
        mock_post.assert_called_once()
        payload = mock_post.call_args[1]['json']
        self.assertIn('活動停止が通報されました', payload['content'])
        self.assertEqual(payload['embeds'][0]['title'], 'テスト集会')
        self.assertEqual(payload['embeds'][0]['fields'][0]['value'], '1')

    def test_webhook_not_sent_when_url_empty(self):
        """Webhook URLが空の場合は送信しない"""
        with patch('community.views.requests.post') as mock_post:
            self.client.post(self.url)
        mock_post.assert_not_called()

    @override_settings(DISCORD_REPORT_WEBHOOK_URL='https://discord.com/api/webhooks/test')
    def test_webhook_failure_does_not_block_report(self):
        """Webhook送信失敗でも通報は成功する"""
        with patch('community.views.requests.post', side_effect=requests_lib.RequestException("timeout")):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CommunityReport.objects.count(), 1)


class GetClientIpTest(TestCase):
    def _make_request(self):
        return self.client.get('/').wsgi_request

    def test_forwarded_for_single_ip(self):
        """X-Forwarded-For に単一IP"""
        request = self._make_request()
        request.META['HTTP_X_FORWARDED_FOR'] = '203.0.113.1'
        self.assertEqual(_get_client_ip(request), '203.0.113.1')

    def test_forwarded_for_multiple_ips(self):
        """X-Forwarded-For に複数IP → 先頭を取得"""
        request = self._make_request()
        request.META['HTTP_X_FORWARDED_FOR'] = '203.0.113.1, 10.0.0.1'
        self.assertEqual(_get_client_ip(request), '203.0.113.1')

    def test_remote_addr_fallback(self):
        """X-Forwarded-For がない場合は REMOTE_ADDR にフォールバック"""
        request = self._make_request()
        request.META.pop('HTTP_X_FORWARDED_FOR', None)
        ip = _get_client_ip(request)
        self.assertIsNotNone(ip)
