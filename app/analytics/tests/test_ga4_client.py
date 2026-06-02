"""ga4_client._build_client の資格情報フォールバックテスト。

ローカル開発: GOOGLE_APPLICATION_CREDENTIALS ファイルから読み込む。
Cloud Run 等: ファイル不在なら compute_engine 資格情報（metadata server 経由）。
"""
from datetime import date
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from analytics import ga4_client


class BuildClientCredentialsTest(TestCase):
    """_build_client が環境に応じて正しい資格情報を選ぶことを確認する。"""

    @override_settings(GOOGLE_APPLICATION_CREDENTIALS='/nonexistent/path/credentials.json')
    @patch.object(ga4_client, 'BetaAnalyticsDataClient')
    @patch.object(ga4_client, 'compute_engine')
    @patch.object(ga4_client, 'service_account')
    def test_falls_back_to_compute_engine_when_credentials_file_missing(
        self, mock_service_account, mock_compute_engine, mock_client_cls,
    ):
        """キーファイルが存在しない場合は compute_engine.Credentials を使う（Cloud Run 想定）。"""
        mock_creds = MagicMock(name='compute_engine_credentials')
        mock_compute_engine.Credentials.return_value = mock_creds

        ga4_client._build_client()

        # service_account 経由は呼ばれない
        mock_service_account.Credentials.from_service_account_file.assert_not_called()
        # compute_engine.Credentials がスコープ付きで呼ばれる
        mock_compute_engine.Credentials.assert_called_once_with(scopes=ga4_client.GA4_SCOPES)
        mock_client_cls.assert_called_once_with(credentials=mock_creds)

    @override_settings(GOOGLE_APPLICATION_CREDENTIALS='')
    @patch.object(ga4_client, 'BetaAnalyticsDataClient')
    @patch.object(ga4_client, 'compute_engine')
    @patch.object(ga4_client, 'service_account')
    def test_falls_back_to_compute_engine_when_credentials_path_empty(
        self, mock_service_account, mock_compute_engine, mock_client_cls,
    ):
        """GOOGLE_APPLICATION_CREDENTIALS が空文字でも compute_engine にフォールバックする。"""
        ga4_client._build_client()

        mock_service_account.Credentials.from_service_account_file.assert_not_called()
        mock_compute_engine.Credentials.assert_called_once_with(scopes=ga4_client.GA4_SCOPES)

    @patch.object(ga4_client.os.path, 'exists', return_value=True)
    @patch.object(ga4_client, 'BetaAnalyticsDataClient')
    @patch.object(ga4_client, 'compute_engine')
    @patch.object(ga4_client, 'service_account')
    @override_settings(GOOGLE_APPLICATION_CREDENTIALS='/app/secret/credentials.json')
    def test_uses_credentials_file_when_present(
        self, mock_service_account, mock_compute_engine, mock_client_cls, _mock_exists,
    ):
        """キーファイルが存在すればそれを使う（ローカル開発想定）。"""
        mock_creds = MagicMock(name='credentials')
        mock_service_account.Credentials.from_service_account_file.return_value = mock_creds

        ga4_client._build_client()

        mock_service_account.Credentials.from_service_account_file.assert_called_once_with(
            '/app/secret/credentials.json',
            scopes=ga4_client.GA4_SCOPES,
        )
        # compute_engine 経由は呼ばれない
        mock_compute_engine.Credentials.assert_not_called()
        mock_client_cls.assert_called_once_with(credentials=mock_creds)


class FetchReportRetryTest(TestCase):
    """GA4 run_report に一時失敗向けリトライを設定することを確認する。"""

    def _empty_response(self):
        response = MagicMock()
        response.row_count = 0
        response.rows = []
        return response

    @patch.object(ga4_client, '_build_client')
    def test_fetch_page_report_passes_retry_policy(self, mock_build_client):
        """ページ別レポート取得は gRPC 一時失敗に備えた retry を渡す。"""
        client = MagicMock()
        client.run_report.return_value = self._empty_response()
        mock_build_client.return_value = client

        rows = ga4_client.fetch_page_report('123456789', date(2026, 5, 31))

        self.assertEqual(rows, [])
        client.run_report.assert_called_once()
        _, kwargs = client.run_report.call_args
        self.assertIs(kwargs['retry'], ga4_client._GA4_RUN_REPORT_RETRY)

    @patch.object(ga4_client, '_build_client')
    def test_fetch_poster_click_report_passes_retry_policy(self, mock_build_client):
        """poster_click レポート取得も同じ retry を渡す。"""
        client = MagicMock()
        client.run_report.return_value = self._empty_response()
        mock_build_client.return_value = client

        rows = ga4_client.fetch_poster_click_report('123456789', date(2026, 5, 31))

        self.assertEqual(rows, [])
        client.run_report.assert_called_once()
        _, kwargs = client.run_report.call_args
        self.assertIs(kwargs['retry'], ga4_client._GA4_RUN_REPORT_RETRY)
