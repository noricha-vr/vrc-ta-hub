"""認証・ソーシャルログイン関連の設定.

AUTH_USER_MODEL / AUTHENTICATION_BACKENDS / django-allauth の ACCOUNT_* /
SOCIALACCOUNT_* / Discord OAuth プロバイダ設定をまとめる。
"""
import os

from .base import DEBUG

AUTH_USER_MODEL = 'user_account.CustomUser'

# allauth backend は ModelBackend 継承で権限チェックも担い、LOGIN_METHODS に username が
# ないため user_name での認証は構造的に無効（ModelBackend が並んでいても USERNAME_FIELD='email'
# のため email 完全一致しか通らず、ユーザー名ログインは復活しない）。
# ModelBackend は email 移行前のセッション互換のためだけに残す: セッションの _auth_user_backend が
# このパスを指しており、一覧から消すと既存ログインが全員無効化される。
# 移行前セッションが失効する SESSION_COOKIE_AGE（既定2週間）経過後に削除してよい（#598）。
AUTHENTICATION_BACKENDS = [
    'allauth.account.auth_backends.AuthenticationBackend',
    'django.contrib.auth.backends.ModelBackend',
]

# django-allauth 設定
ACCOUNT_LOGIN_METHODS = {'email'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'password1*', 'password2*']
ACCOUNT_EMAIL_VERIFICATION = 'mandatory'
ACCOUNT_CHANGE_EMAIL = True
# 同一 email は5回/5分、同一IPは10回/分で制限する。allauth の既定値を
# 明示して、ライブラリ更新でブルートフォース対策の強度が変わらないようにする。
ACCOUNT_RATE_LIMITS = {
    'login_failed': '10/m/ip,5/300s/key',
}
# Confirmation links only resume the signup login in the browser that started
# registration. This preserves a validated ``next`` without turning a leaked,
# reusable confirmation link into a session-independent login link.
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
ACCOUNT_USER_MODEL_USERNAME_FIELD = 'user_name'
ACCOUNT_SESSION_REMEMBER = None  # ユーザーに選択させる
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_LOGIN_ON_GET = True
ACCOUNT_ADAPTER = 'user_account.adapters.CustomAccountAdapter'
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
