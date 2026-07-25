"""X API OAuth 1.0a でアクセストークンを取得する管理コマンド。

使い方:
  docker compose exec vrc-ta-hub python manage.py generate_x_token

取得したトークンは stdout に出さず、パーミッション 0600 のファイルへ書き出す
（ターミナルのスクロールバック・CI ログ経由での秘密情報漏洩を防ぐため）。
"""

import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from requests_oauthlib import OAuth1Session

X_REQUEST_TOKEN_URL = "https://api.x.com/oauth/request_token"
X_AUTHORIZE_URL = "https://api.x.com/oauth/authorize"
X_ACCESS_TOKEN_URL = "https://api.x.com/oauth/access_token"

# 秘密情報ファイルは所有者のみ読み書き可能にする
TOKEN_FILE_MODE = 0o600
DEFAULT_TOKEN_FILENAME = ".x-token.env"


class Command(BaseCommand):
    help = "X API OAuth 1.0a でアクセストークンを取得する"

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            dest="output",
            default=None,
            help=(
                "トークンの書き出し先パス"
                f"（省略時は BASE_DIR/{DEFAULT_TOKEN_FILENAME}）"
            ),
        )

    def handle(self, *args, **options):
        consumer_key = os.environ.get("X_API_KEY")
        consumer_secret = os.environ.get("X_API_SECRET")

        if not consumer_key or not consumer_secret:
            raise CommandError(
                "X_API_KEY と X_API_SECRET を .env.local に設定してください"
            )

        # Step 1: Request Token
        oauth = OAuth1Session(
            consumer_key, client_secret=consumer_secret, callback_uri="oob",
        )
        try:
            request_token = oauth.fetch_request_token(X_REQUEST_TOKEN_URL)
        except Exception as e:
            raise CommandError(f"Request Token の取得に失敗: {e}")

        auth_url = oauth.authorization_url(X_AUTHORIZE_URL)
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("=" * 60))
        self.stdout.write(self.style.WARNING(
            "以下のURLを投稿したいアカウントでログインしたブラウザで開いてください:"
        ))
        self.stdout.write(self.style.WARNING("=" * 60))
        self.stdout.write(self.style.SUCCESS(auth_url))
        self.stdout.write("")

        verifier = input("表示されたPINを入力してください: ").strip()
        if not verifier:
            raise CommandError("PINが入力されませんでした")

        # Step 2: Access Token
        oauth = OAuth1Session(
            consumer_key,
            client_secret=consumer_secret,
            resource_owner_key=request_token["oauth_token"],
            resource_owner_secret=request_token["oauth_token_secret"],
            verifier=verifier,
        )
        try:
            tokens = oauth.fetch_access_token(X_ACCESS_TOKEN_URL)
        except Exception as e:
            raise CommandError(f"Access Token の取得に失敗: {e}")

        output_path = self._resolve_output_path(options["output"])
        self._write_token_file(output_path, tokens)

        screen_name = tokens.get("screen_name", "不明")
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(self.style.SUCCESS(
            f"認証成功! アカウント: @{screen_name}"
        ))
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write("")
        self.stdout.write(f"トークンを書き出しました: {output_path}")
        self.stdout.write(
            "ファイルの中身を .env.local に追記し、不要になったら削除してください"
        )

    def _resolve_output_path(self, output: str | None) -> Path:
        if output:
            return Path(output).expanduser()
        return Path(settings.BASE_DIR) / DEFAULT_TOKEN_FILENAME

    def _write_token_file(self, path: Path, tokens: dict) -> None:
        """トークンを 0600 のファイルへ書き出す。

        既存ファイルのパーミッションを引き継がないよう、書き込み前に
        os.open(O_CREAT) のモード指定と chmod の両方で 0600 を保証する。
        """
        content = (
            f"X_ACCESS_TOKEN={tokens['oauth_token']}\n"
            f"X_ACCESS_TOKEN_SECRET={tokens['oauth_token_secret']}\n"
        )
        try:
            fd = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                TOKEN_FILE_MODE,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.chmod(path, TOKEN_FILE_MODE)
        except OSError as e:
            raise CommandError(f"トークンの書き出しに失敗: {path}: {e}")
