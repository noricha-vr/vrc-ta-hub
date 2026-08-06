"""/health エンドポイントのテスト

Cloud Run の readiness/liveness probe 想定の挙動を検証する。
- 正常時: 200 / status=ok
- DB 切断時: 503 / status=ng
- cache 失敗時: cache=ng でも生存判定 (status=ok) は維持
"""

import json
from unittest.mock import patch

from django.test import Client, TestCase, override_settings


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
class HealthCheckTest(TestCase):
    """/health エンドポイントの挙動テスト"""

    def setUp(self):
        self.client = Client()

    def test_health_returns_ok_when_db_and_cache_ok(self):
        """通常時は 200 / status=ok / db=ok / cache=ok を返す"""
        response = self.client.get('/health')

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['db'], 'ok')
        self.assertEqual(payload['cache'], 'ok')
        self.assertEqual(payload['shared_cache'], 'ok')

    def test_health_marks_shared_cache_ng_when_default_cache_unavailable(self):
        """default cache が使えないと shared_cache=ng になる

        Cloud Run の default は DatabaseCache で、テーブル未作成（migration 未適用）だと
        ここが落ちる。cache フィールドは常に LocMem を見るので検知できない。
        """
        with patch('ta_hub.health.caches') as mock_caches:
            mock_caches.__getitem__.side_effect = Exception('no such table')
            response = self.client.get('/health')

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['shared_cache'], 'ng')

    def test_health_returns_503_when_db_unreachable(self):
        """DB 切断時は 503 / status=ng / db=ng を返す（probe で外す）"""
        with patch(
            'ta_hub.health.connection.ensure_connection',
            side_effect=Exception('db down'),
        ):
            response = self.client.get('/health')

        self.assertEqual(response.status_code, 503)
        payload = json.loads(response.content)
        self.assertEqual(payload['status'], 'ng')
        self.assertEqual(payload['db'], 'ng')

    def test_health_keeps_ok_when_only_cache_fails(self):
        """cache 失敗時も status=ok を返す（cache 未設定環境でも生存判定したい）"""
        with patch(
            'ta_hub.health._get_health_cache',
            side_effect=Exception('cache down'),
        ):
            response = self.client.get('/health')

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['db'], 'ok')
        self.assertEqual(payload['cache'], 'ng')

    def test_health_cache_roundtrip_mismatch_marks_cache_ng(self):
        """cache.get が想定外の値を返すと cache=ng になる（status は ok のまま）"""
        with patch('ta_hub.health._get_health_cache') as get_cache:
            get_cache.return_value.get.return_value = 'unexpected'
            response = self.client.get('/health')

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['cache'], 'ng')
