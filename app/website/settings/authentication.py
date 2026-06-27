"""認証・ソーシャルログイン関連の設定.

AUTH_USER_MODEL / AUTHENTICATION_BACKENDS / django-allauth の ACCOUNT_* /
SOCIALACCOUNT_* / Discord OAuth プロバイダ設定をまとめる。
"""
import os

from .base import DEBUG

AUTH_USER_MODEL = 'user_account.CustomUser'

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

# django-allauth 設定
ACCOUNT_EMAIL_REQUIRED = False
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_AUTHENTICATION_METHOD = 'username'
ACCOUNT_EMAIL_VERIFICATION = 'none'
ACCOUNT_USER_MODEL_USERNAME_FIELD = 'user_name'
ACCOUNT_SESSION_REMEMBER = None  # ユーザーに選択させる
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_LOGIN_ON_GET = True
SOCIALACCOUNT_ADAPTER = 'user_account.adapters.CustomSocialAccountAdapter'

# OAuth callback URLのプロトコル（本番: https、開発: http）
ACCOUNT_DEFAULT_HTTP_PROTOCOL = os.environ.get('ACCOUNT_DEFAULT_HTTP_PROTOCOL', 'https')

# Discord OAuth設定
DISCORD_CLIENT_ID = os.environ.get('DISCORD_CLIENT_ID', '')
DISCORD_CLIENT_SECRET = os.environ.get('DISCORD_CLIENT_SECRET', '')

SOCIALACCOUNT_PROVIDERS = {
    'discord': {
        'SCOPE': ['identify', 'email'],
    }
}

# 環境変数が設定されている場合のみAPPS設定を追加
if DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET:
    SOCIALACCOUNT_PROVIDERS['discord']['APPS'] = [
        {
            'client_id': DISCORD_CLIENT_ID,
            'secret': DISCORD_CLIENT_SECRET,
            'key': '',
        }
    ]

SOCIALACCOUNT_FORMS = {
    'signup': 'user_account.forms.CustomSocialSignupForm',
}

# ソーシャルアカウントの接続解除（disconnect）を試みた場合のリダイレクト先
# 削除ボタンはテンプレートで非表示にするが、直接アクセスされた場合の保険
SOCIALACCOUNT_DISCONNECT_REDIRECT_URL = '/account/settings/'

# AIエージェントのローカル確認用。DEBUG=False では環境変数が true でも無効化する。
DEBUG_LOGIN_SKIP = DEBUG and os.environ.get('DEBUG_LOGIN_SKIP', '').lower() == 'true'
DEBUG_LOGIN_SKIP_USER_NAME = os.environ.get('DEBUG_LOGIN_SKIP_USER_NAME', 'ai_agent')
DEBUG_LOGIN_SKIP_USER_EMAIL = os.environ.get(
    'DEBUG_LOGIN_SKIP_USER_EMAIL',
    'ai-agent@example.local',
)
