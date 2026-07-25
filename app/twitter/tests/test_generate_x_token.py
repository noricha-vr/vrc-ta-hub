"""generate_x_token 管理コマンドのテスト（OAuth 1.0a PINフロー）。

OAuth フロー全体は外部API依存のため、各ステップをモックしてテストする。
失敗時は CommandError（exit code 1）で終了し、成功時もトークン値を
stdout に出さず 0600 のファイルへ書き出すことを検証する。
"""

import os
import stat
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase


class GenerateXTokenCommandTest(TestCase):
    """generate_x_token コマンドのユニットテスト。"""

    OAUTH1_ENV = {
        "X_API_KEY": "test-api-key",
        "X_API_SECRET": "test-api-secret",
    }

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.output_path = Path(self._tmpdir.name) / "x-token.env"

    def _call_command(self, **kwargs):
        """コマンドを呼び出し、stdout/stderr を返す。"""
        out = StringIO()
        err = StringIO()
        kwargs.setdefault("output", str(self.output_path))
        call_command("generate_x_token", stdout=out, stderr=err, **kwargs)
        return out.getvalue(), err.getvalue()

    def _mock_oauth_sessions(self, access_tokens):
        """Request Token / Access Token 用のセッションモックを返す。"""
        request_session = MagicMock()
        request_session.fetch_request_token.return_value = {
            "oauth_token": "req-token",
            "oauth_token_secret": "req-secret",
        }
        request_session.authorization_url.return_value = (
            "https://api.x.com/oauth/authorize?oauth_token=req-token"
        )

        access_session = MagicMock()
        access_session.fetch_access_token.return_value = access_tokens
        return [request_session, access_session]

    @patch.dict(
        "os.environ", {"X_API_KEY": "", "X_API_SECRET": ""}, clear=False
    )
    def test_missing_credentials_raises_command_error(self):
        """API Key/Secret が未設定なら CommandError で異常終了する。"""
        with self.assertRaises(CommandError) as ctx:
            self._call_command()
        self.assertIn("X_API_KEY", str(ctx.exception))
        self.assertIn("X_API_SECRET", str(ctx.exception))

    @patch.dict("os.environ", OAUTH1_ENV, clear=False)
    @patch(
        "twitter.management.commands.generate_x_token.OAuth1Session"
    )
    def test_request_token_failure_raises_command_error(self, mock_session_cls):
        """Request Token 取得失敗時に CommandError で異常終了する。"""
        mock_session = MagicMock()
        mock_session.fetch_request_token.side_effect = Exception(
            "Request token failed"
        )
        mock_session_cls.return_value = mock_session

        with self.assertRaises(CommandError) as ctx:
            self._call_command()
        self.assertIn("Request Token の取得に失敗", str(ctx.exception))

    @patch.dict("os.environ", OAUTH1_ENV, clear=False)
    @patch("builtins.input", return_value="")
    @patch(
        "twitter.management.commands.generate_x_token.OAuth1Session"
    )
    def test_empty_pin_raises_command_error(self, mock_session_cls, mock_input):
        """PINが空なら CommandError で異常終了する。"""
        mock_session = MagicMock()
        mock_session.fetch_request_token.return_value = {
            "oauth_token": "req-token",
            "oauth_token_secret": "req-secret",
        }
        mock_session.authorization_url.return_value = (
            "https://api.x.com/oauth/authorize?oauth_token=req-token"
        )
        mock_session_cls.return_value = mock_session

        with self.assertRaises(CommandError) as ctx:
            self._call_command()
        self.assertIn("PINが入力されませんでした", str(ctx.exception))

    @patch.dict("os.environ", OAUTH1_ENV, clear=False)
    @patch("builtins.input", return_value="123456")
    @patch(
        "twitter.management.commands.generate_x_token.OAuth1Session"
    )
    def test_access_token_failure_raises_command_error(self, mock_session_cls, mock_input):
        """Access Token 取得失敗時に CommandError で異常終了する。"""
        request_session = MagicMock()
        request_session.fetch_request_token.return_value = {
            "oauth_token": "req-token",
            "oauth_token_secret": "req-secret",
        }
        request_session.authorization_url.return_value = (
            "https://api.x.com/oauth/authorize?oauth_token=req-token"
        )

        access_session = MagicMock()
        access_session.fetch_access_token.side_effect = Exception(
            "Access token failed"
        )

        mock_session_cls.side_effect = [request_session, access_session]

        with self.assertRaises(CommandError) as ctx:
            self._call_command()
        self.assertIn("Access Token の取得に失敗", str(ctx.exception))

    @patch.dict("os.environ", OAUTH1_ENV, clear=False)
    @patch("builtins.input", return_value="654321")
    @patch(
        "twitter.management.commands.generate_x_token.OAuth1Session"
    )
    def test_successful_flow_does_not_print_tokens(self, mock_session_cls, mock_input):
        """正常フローでトークン値が stdout/stderr に出ないこと。"""
        mock_session_cls.side_effect = self._mock_oauth_sessions({
            "oauth_token": "access-token-123",
            "oauth_token_secret": "access-secret-456",
            "screen_name": "vrc_ta_hub",
        })

        out, err = self._call_command()

        self.assertIn("@vrc_ta_hub", out)
        self.assertIn("認証成功", out)
        self.assertNotIn("access-token-123", out)
        self.assertNotIn("access-secret-456", out)
        self.assertNotIn("access-token-123", err)
        self.assertNotIn("access-secret-456", err)
        self.assertIn(str(self.output_path), out)

    @patch.dict("os.environ", OAUTH1_ENV, clear=False)
    @patch("builtins.input", return_value="654321")
    @patch(
        "twitter.management.commands.generate_x_token.OAuth1Session"
    )
    def test_successful_flow_writes_token_file_with_0600(self, mock_session_cls, mock_input):
        """トークンは 0600 のファイルへ書き出される。"""
        mock_session_cls.side_effect = self._mock_oauth_sessions({
            "oauth_token": "access-token-123",
            "oauth_token_secret": "access-secret-456",
            "screen_name": "vrc_ta_hub",
        })

        self._call_command()

        content = self.output_path.read_text(encoding="utf-8")
        self.assertIn("X_ACCESS_TOKEN=access-token-123", content)
        self.assertIn("X_ACCESS_TOKEN_SECRET=access-secret-456", content)

        mode = stat.S_IMODE(os.stat(self.output_path).st_mode)
        self.assertEqual(mode, 0o600)

    @patch.dict("os.environ", OAUTH1_ENV, clear=False)
    @patch("builtins.input", return_value="654321")
    @patch(
        "twitter.management.commands.generate_x_token.OAuth1Session"
    )
    def test_existing_file_permissions_are_tightened(self, mock_session_cls, mock_input):
        """既存ファイルが緩いパーミッションでも 0600 に強制される。"""
        self.output_path.write_text("stale", encoding="utf-8")
        os.chmod(self.output_path, 0o644)

        mock_session_cls.side_effect = self._mock_oauth_sessions({
            "oauth_token": "token-no-name",
            "oauth_token_secret": "secret-no-name",
        })

        out, _ = self._call_command()

        self.assertIn("認証成功", out)
        mode = stat.S_IMODE(os.stat(self.output_path).st_mode)
        self.assertEqual(mode, 0o600)
        self.assertNotIn("token-no-name", out)
