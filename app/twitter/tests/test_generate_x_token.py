"""generate_x_token 管理コマンドのテスト（OAuth 1.0a PINフロー）。

OAuth フロー全体は外部API依存のため、各ステップをモックしてテストする。
"""

from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase


class GenerateXTokenCommandTest(TestCase):
    """generate_x_token コマンドのユニットテスト。"""

    OAUTH1_ENV = {
        "X_API_KEY": "test-api-key",
        "X_API_SECRET": "test-api-secret",
    }

    def _call_command(self, **kwargs):
        """コマンドを呼び出し、stdout/stderr を返す。"""
        out = StringIO()
        err = StringIO()
        call_command("generate_x_token", stdout=out, stderr=err, **kwargs)
        return out.getvalue(), err.getvalue()

    @patch.dict(
        "os.environ", {"X_API_KEY": "", "X_API_SECRET": ""}, clear=False
    )
    def test_missing_credentials_shows_error(self):
        """API Key/Secret が未設定の場合、エラーメッセージを出力する。"""
        _, err = self._call_command()
        self.assertIn("X_API_KEY", err)
        self.assertIn("X_API_SECRET", err)

    @patch.dict("os.environ", OAUTH1_ENV, clear=False)
    @patch(
        "twitter.management.commands.generate_x_token.OAuth1Session"
    )
    def test_request_token_failure_shows_error(self, mock_session_cls):
        """Request Token 取得失敗時にエラーメッセージを出力する。"""
        mock_session = MagicMock()
        mock_session.fetch_request_token.side_effect = Exception(
            "Request token failed"
        )
        mock_session_cls.return_value = mock_session

        _, err = self._call_command()
        self.assertIn("Request Token の取得に失敗", err)

    @patch.dict("os.environ", OAUTH1_ENV, clear=False)
    @patch("builtins.input", return_value="")
    @patch(
        "twitter.management.commands.generate_x_token.OAuth1Session"
    )
    def test_empty_pin_shows_error(self, mock_session_cls, mock_input):
        """PINが空の場合にエラーメッセージを出力する。"""
        mock_session = MagicMock()
        mock_session.fetch_request_token.return_value = {
            "oauth_token": "req-token",
            "oauth_token_secret": "req-secret",
        }
        mock_session.authorization_url.return_value = "https://api.x.com/oauth/authorize?oauth_token=req-token"
        mock_session_cls.return_value = mock_session

        _, err = self._call_command()
        self.assertIn("PINが入力されませんでした", err)

    @patch.dict("os.environ", OAUTH1_ENV, clear=False)
    @patch("builtins.input", return_value="123456")
    @patch(
        "twitter.management.commands.generate_x_token.OAuth1Session"
    )
    def test_access_token_failure_shows_error(self, mock_session_cls, mock_input):
        """Access Token 取得失敗時にエラーメッセージを出力する。"""
        # 1回目: Request Token 取得用セッション
        request_session = MagicMock()
        request_session.fetch_request_token.return_value = {
            "oauth_token": "req-token",
            "oauth_token_secret": "req-secret",
        }
        request_session.authorization_url.return_value = "https://api.x.com/oauth/authorize?oauth_token=req-token"

        # 2回目: Access Token 取得用セッション
        access_session = MagicMock()
        access_session.fetch_access_token.side_effect = Exception(
            "Access token failed"
        )

        mock_session_cls.side_effect = [request_session, access_session]

        _, err = self._call_command()
        self.assertIn("Access Token の取得に失敗", err)

    @patch.dict("os.environ", OAUTH1_ENV, clear=False)
    @patch("builtins.input", return_value="654321")
    @patch(
        "twitter.management.commands.generate_x_token.OAuth1Session"
    )
    def test_successful_flow_outputs_tokens(self, mock_session_cls, mock_input):
        """正常フローでトークンが標準出力に表示される。"""
        # 1回目: Request Token 取得用セッション
        request_session = MagicMock()
        request_session.fetch_request_token.return_value = {
            "oauth_token": "req-token",
            "oauth_token_secret": "req-secret",
        }
        request_session.authorization_url.return_value = "https://api.x.com/oauth/authorize?oauth_token=req-token"

        # 2回目: Access Token 取得用セッション
        access_session = MagicMock()
        access_session.fetch_access_token.return_value = {
            "oauth_token": "access-token-123",
            "oauth_token_secret": "access-secret-456",
            "screen_name": "vrc_ta_hub",
        }

        mock_session_cls.side_effect = [request_session, access_session]

        out, _ = self._call_command()

        self.assertIn("@vrc_ta_hub", out)
        self.assertIn("認証成功", out)
        self.assertIn("X_ACCESS_TOKEN=access-token-123", out)
        self.assertIn("X_ACCESS_TOKEN_SECRET=access-secret-456", out)

    @patch.dict("os.environ", OAUTH1_ENV, clear=False)
    @patch("builtins.input", return_value="654321")
    @patch(
        "twitter.management.commands.generate_x_token.OAuth1Session"
    )
    def test_successful_flow_without_screen_name(self, mock_session_cls, mock_input):
        """screen_name がレスポンスにない場合でもトークンが出力される。"""
        request_session = MagicMock()
        request_session.fetch_request_token.return_value = {
            "oauth_token": "req-token",
            "oauth_token_secret": "req-secret",
        }
        request_session.authorization_url.return_value = "https://api.x.com/oauth/authorize?oauth_token=req-token"

        access_session = MagicMock()
        access_session.fetch_access_token.return_value = {
            "oauth_token": "token-no-name",
            "oauth_token_secret": "secret-no-name",
        }

        mock_session_cls.side_effect = [request_session, access_session]

        out, _ = self._call_command()

        self.assertIn("認証成功", out)
        self.assertIn("X_ACCESS_TOKEN=token-no-name", out)
        self.assertIn("X_ACCESS_TOKEN_SECRET=secret-no-name", out)
