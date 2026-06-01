"""ga4_client._build_client の資格情報フォールバックテスト。

ローカル開発: GOOGLE_APPLICATION_CREDENTIALS ファイルから読み込む。
Cloud Run 等: ファイル不在なら ADC（Application Default Credentials）を使う。
"""
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from analytics import ga4_client


class BuildClientCredentialsTest(TestCase):
    """_build_client が環境に応じて正しい資格情報を選ぶことを確認する。"""

    @override_settings(GOOGLE_APPLICATION_CREDENTIALS='/nonexistent/path/credentials.json')
    @patch.object(ga4_client, 'BetaAnalyticsDataClient')
    @patch.object(ga4_client, 'service_account')
    def test_falls_back_to_adc_when_credentials_file_missing(
        self, mock_service_account, mock_client_cls,
    ):
        """キーファイルが存在しない場合は ADC を使う（Cloud Run 想定）。"""
        ga4_client._build_client()

        # service_account.Credentials.from_service_account_file は呼ばれない
        mock_service_account.Credentials.from_service_account_file.assert_not_called()
        # BetaAnalyticsDataClient は引数なしで呼ばれる（ADC を自動取得）
        mock_client_cls.assert_called_once_with()

    @override_settings(GOOGLE_APPLICATION_CREDENTIALS='')
    @patch.object(ga4_client, 'BetaAnalyticsDataClient')
    @patch.object(ga4_client, 'service_account')
    def test_falls_back_to_adc_when_credentials_path_empty(
        self, mock_service_account, mock_client_cls,
    ):
        """GOOGLE_APPLICATION_CREDENTIALS が空文字でも ADC にフォールバックする。"""
        ga4_client._build_client()

        mock_service_account.Credentials.from_service_account_file.assert_not_called()
        mock_client_cls.assert_called_once_with()

    @patch.object(ga4_client.os.path, 'exists', return_value=True)
    @patch.object(ga4_client, 'BetaAnalyticsDataClient')
    @patch.object(ga4_client, 'service_account')
    @override_settings(GOOGLE_APPLICATION_CREDENTIALS='/app/secret/credentials.json')
    def test_uses_credentials_file_when_present(
        self, mock_service_account, mock_client_cls, _mock_exists,
    ):
        """キーファイルが存在すればそれを使う（ローカル開発想定）。"""
        mock_creds = MagicMock(name='credentials')
        mock_service_account.Credentials.from_service_account_file.return_value = mock_creds

        ga4_client._build_client()

        mock_service_account.Credentials.from_service_account_file.assert_called_once_with(
            '/app/secret/credentials.json',
            scopes=ga4_client.GA4_SCOPES,
        )
        mock_client_cls.assert_called_once_with(credentials=mock_creds)
