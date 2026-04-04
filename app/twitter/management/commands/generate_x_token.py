"""X API OAuth 1.0a でアクセストークンを取得する管理コマンド。

使い方:
  docker compose exec vrc-ta-hub python manage.py generate_x_token
"""

import os

from django.core.management.base import BaseCommand
from requests_oauthlib import OAuth1Session

X_REQUEST_TOKEN_URL = "https://api.x.com/oauth/request_token"
X_AUTHORIZE_URL = "https://api.x.com/oauth/authorize"
X_ACCESS_TOKEN_URL = "https://api.x.com/oauth/access_token"


class Command(BaseCommand):
    help = "X API OAuth 1.0a でアクセストークンを取得する"

    def handle(self, *args, **options):
        consumer_key = os.environ.get("X_API_KEY")
        consumer_secret = os.environ.get("X_API_SECRET")

        if not consumer_key or not consumer_secret:
            self.stderr.write(self.style.ERROR(
                "X_API_KEY と X_API_SECRET を .env.local に設定してください"
            ))
            return

        # Step 1: Request Token
        oauth = OAuth1Session(
            consumer_key, client_secret=consumer_secret, callback_uri="oob",
        )
        try:
            request_token = oauth.fetch_request_token(X_REQUEST_TOKEN_URL)
        except Exception as e:
            self.stderr.write(self.style.ERROR(
                f"Request Token の取得に失敗: {e}"
            ))
            return

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
            self.stderr.write(self.style.ERROR("PINが入力されませんでした"))
            return

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
            self.stderr.write(self.style.ERROR(
                f"Access Token の取得に失敗: {e}"
            ))
            return

        screen_name = tokens.get("screen_name", "不明")
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(self.style.SUCCESS(
            f"認証成功! アカウント: @{screen_name}"
        ))
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write("")
        self.stdout.write("以下を .env.local に追加してください:")
        self.stdout.write(f"X_ACCESS_TOKEN={tokens['oauth_token']}")
        self.stdout.write(f"X_ACCESS_TOKEN_SECRET={tokens['oauth_token_secret']}")
