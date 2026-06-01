"""ga4_client._build_client の資格情報フォールバックテスト。

ローカル開発: GOOGLE_APPLICATION_CREDENTIALS ファイルから読み込む。
Cloud Run 等: ファイル不在なら compute_engine 資格情報（metadata server 経由）。
"""
from datetime import date
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from google.api_core.exceptions import InvalidArgument

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


class FetchPosterClickReportTest(TestCase):
    """fetch_poster_click_report の GA4 API エラー耐性を確認する。"""

    @patch.object(ga4_client, '_build_client')
    def test_missing_custom_dimension_returns_empty_result(self, mock_build_client):
        """community_id 未登録時は poster_click だけ空結果にして同期全体を守る。"""
        mock_client = MagicMock()
        mock_client.run_report.side_effect = InvalidArgument(
            'Field customEvent:community_id is not a valid dimension.'
        )
        mock_build_client.return_value = mock_client

        with self.assertLogs('analytics', level='WARNING') as logs:
            result = ga4_client.fetch_poster_click_report(
                '123456789',
                date(2026, 6, 1),
            )

        self.assertEqual(result, [])
        self.assertIn(
            'GA4 poster_click custom dimension unavailable',
            '\n'.join(logs.output),
        )

    @patch.object(ga4_client, '_build_client')
    def test_other_invalid_argument_is_raised(self, mock_build_client):
        """community_id 未登録以外の GA4 InvalidArgument は呼び出し側へ伝える。"""
        mock_client = MagicMock()
        mock_client.run_report.side_effect = InvalidArgument(
            'Field unknownMetric is not a valid metric.'
        )
        mock_build_client.return_value = mock_client

        with self.assertRaises(InvalidArgument):
            ga4_client.fetch_poster_click_report('123456789', date(2026, 6, 1))
