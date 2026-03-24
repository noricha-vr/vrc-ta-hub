from django.test import TestCase, RequestFactory

from ta_hub.utils import get_client_ip


class GetClientIpTest(TestCase):
    """Cloud Run 環境での get_client_ip の動作を検証する。

    Cloud Run では Google LB が X-Forwarded-For の末尾にクライアント IP を追加する。
    構成: [ユーザー偽装IP, ..., クライアントIP, Google LB IP]
    """

    def setUp(self):
        self.factory = RequestFactory()

    def _make_request(self):
        return self.factory.get('/')

    def test_single_ip(self):
        """単一 IP: そのまま返す"""
        request = self._make_request()
        request.META['HTTP_X_FORWARDED_FOR'] = '203.0.113.1'
        self.assertEqual(get_client_ip(request), '203.0.113.1')

    def test_two_ips_returns_second_to_last(self):
        """2つの IP: クライアント + LB -> 末尾から2番目（クライアント IP）を返す"""
        request = self._make_request()
        request.META['HTTP_X_FORWARDED_FOR'] = '203.0.113.1, 10.128.0.1'
        self.assertEqual(get_client_ip(request), '203.0.113.1')

    def test_three_ips_spoofed_header(self):
        """3つの IP: 偽装 + クライアント + LB -> 末尾から2番目（クライアント IP）を返す"""
        request = self._make_request()
        request.META['HTTP_X_FORWARDED_FOR'] = '1.2.3.4, 203.0.113.1, 10.128.0.1'
        self.assertEqual(get_client_ip(request), '203.0.113.1')

    def test_four_ips_multiple_spoofed(self):
        """4つの IP: 偽装x2 + クライアント + LB -> 末尾から2番目を返す"""
        request = self._make_request()
        request.META['HTTP_X_FORWARDED_FOR'] = '1.2.3.4, 5.6.7.8, 203.0.113.1, 10.128.0.1'
        self.assertEqual(get_client_ip(request), '203.0.113.1')

    def test_remote_addr_fallback(self):
        """X-Forwarded-For がない場合は REMOTE_ADDR にフォールバック"""
        request = self._make_request()
        request.META.pop('HTTP_X_FORWARDED_FOR', None)
        request.META['REMOTE_ADDR'] = '127.0.0.1'
        self.assertEqual(get_client_ip(request), '127.0.0.1')

    def test_no_ip_info_returns_default(self):
        """IP 情報が一切ない場合はデフォルト値を返す"""
        request = self._make_request()
        request.META.pop('HTTP_X_FORWARDED_FOR', None)
        request.META.pop('REMOTE_ADDR', None)
        self.assertEqual(get_client_ip(request), '0.0.0.0')

    def test_whitespace_handling(self):
        """IP アドレスの前後の空白が除去される"""
        request = self._make_request()
        request.META['HTTP_X_FORWARDED_FOR'] = ' 203.0.113.1 , 10.128.0.1 '
        self.assertEqual(get_client_ip(request), '203.0.113.1')

    def test_empty_forwarded_for_falls_back(self):
        """空の X-Forwarded-For は REMOTE_ADDR にフォールバック"""
        request = self._make_request()
        request.META['HTTP_X_FORWARDED_FOR'] = ''
        request.META['REMOTE_ADDR'] = '192.168.1.1'
        self.assertEqual(get_client_ip(request), '192.168.1.1')
