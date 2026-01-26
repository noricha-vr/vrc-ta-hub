"""BigQueryクライアントの遅延初期化のテスト。

GCP認証情報がない環境でもモジュールがインポートできることを確認する。
"""
import os
from unittest import TestCase
from unittest.mock import patch, MagicMock


class TestBigQueryLazyInit(TestCase):
    """BigQueryクライアントの遅延初期化のテスト。"""

    def test_module_imports_without_gcp_credentials(self):
        """GCP認証情報がなくてもモジュールがインポートできること。

        モジュールレベルでgoogle.auth.default()が呼ばれないことを確認する。
        """
        # views.pyがインポート済みの場合でも、グローバル変数が
        # Noneのままであることを確認
        from event.views import _bigquery_client, _bigquery_project

        # _get_bigquery_clientが呼ばれるまでNoneのまま
        self.assertIsNone(_bigquery_client)
        self.assertIsNone(_bigquery_project)

    @patch.dict(os.environ, {'TESTING': '1'})
    def test_get_bigquery_client_returns_mock_in_testing(self):
        """TESTING環境変数が設定されている場合はMockを返すこと。"""
        # キャッシュをリセットするためにグローバル変数をリセット
        import event.views as views_module
        views_module._bigquery_client = None
        views_module._bigquery_project = None

        try:
            from event.views import _get_bigquery_client

            client, project = _get_bigquery_client()

            # TESTINGモードではMockが返される
            self.assertEqual(project, 'test-project')
            self.assertIsNotNone(client)
        finally:
            # テスト後にクリーンアップ
            views_module._bigquery_client = None
            views_module._bigquery_project = None

    @patch.dict(os.environ, {'TESTING': '1'})
    def test_get_bigquery_client_is_cached(self):
        """_get_bigquery_clientが結果をキャッシュすること。"""
        import event.views as views_module
        views_module._bigquery_client = None
        views_module._bigquery_project = None

        try:
            from event.views import _get_bigquery_client

            # 1回目の呼び出し
            client1, project1 = _get_bigquery_client()

            # 2回目の呼び出し - 同じインスタンスが返されるはず
            client2, project2 = _get_bigquery_client()

            self.assertIs(client1, client2)
            self.assertEqual(project1, project2)
        finally:
            # テスト後にクリーンアップ
            views_module._bigquery_client = None
            views_module._bigquery_project = None
