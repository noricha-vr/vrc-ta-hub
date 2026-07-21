"""外部API・メール送信の設定.

Google Calendar / YouTube / Gemini / GA4 / OpenRouter / Amazon SES / Discord Webhook
の認証情報・モデル名・宛先などをまとめる。
"""
import os

from .base import DEBUG, _mask, _settings_logger

# Google Calendar APIの設定
GOOGLE_CALENDAR_CREDENTIALS = os.getenv('GOOGLE_CALENDAR_CREDENTIALS', '/app/credentials.json')

# Google API
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')
assert GOOGLE_API_KEY is not None, 'Please set GOOGLE_API_KEY'

GOOGLE_CALENDAR_ID = os.environ.get('GOOGLE_CALENDAR_ID')
assert GOOGLE_CALENDAR_ID is not None, 'Please set GOOGLE_CALENDAR_ID'
_settings_logger.info('GOOGLE_CALENDAR_ID: %s', _mask(GOOGLE_CALENDAR_ID))
if GOOGLE_CALENDAR_ID.startswith('d80eac'):
    _settings_logger.info('Debug mode: GOOGLE_CALENDAR_ID starts with d80eac')
else:
    _settings_logger.info('Production mode: GOOGLE_CALENDAR_ID')

# Gemini API
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
assert GEMINI_API_KEY is not None, 'Please set GEMINI_API_KEY'
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'google/gemini-2.5-flash-lite-preview-06-17')
_settings_logger.info('GEMINI_MODEL: %s', GEMINI_MODEL)

# GA4 Data API（ページ別アクセス解析）
# GA4_PROPERTY_ID は Data API 用の数値ID。base.html の measurement ID G-6BN9EHVMRW とは別物
GA4_PROPERTY_ID = os.environ.get('GA4_PROPERTY_ID', '444114283')
GOOGLE_APPLICATION_CREDENTIALS = os.getenv(
    'GOOGLE_APPLICATION_CREDENTIALS', '/app/secret/credentials.json'
)

REQUEST_TOKEN = os.environ.get('REQUEST_TOKEN')
assert REQUEST_TOKEN is not None, 'Please set REQUEST_TOKEN'

# ローカルPCから実行する集会活動監視専用API。
# 他のバッチ用 REQUEST_TOKEN と分離し、未設定時はエンドポイントを fail-closed にする。
COMMUNITY_ACTIVITY_MONITOR_TOKEN = os.environ.get('COMMUNITY_ACTIVITY_MONITOR_TOKEN', '')
COMMUNITY_ACTIVITY_INACTIVE_DAYS = int(os.environ.get('COMMUNITY_ACTIVITY_INACTIVE_DAYS', '90'))
COMMUNITY_ACTIVITY_REQUIRED_CHECKS = int(os.environ.get('COMMUNITY_ACTIVITY_REQUIRED_CHECKS', '2'))
COMMUNITY_ACTIVITY_MIN_INACTIVE_CONFIDENCE = float(
    os.environ.get('COMMUNITY_ACTIVITY_MIN_INACTIVE_CONFIDENCE', '0.75')
)
COMMUNITY_ACTIVITY_EXPLICIT_END_CONFIDENCE = float(
    os.environ.get('COMMUNITY_ACTIVITY_EXPLICIT_END_CONFIDENCE', '0.90')
)

# メール設定 (Amazon SES)
if DEBUG:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
else:
    EMAIL_BACKEND = 'django_ses.SESBackend'
AWS_SES_REGION_NAME = os.environ.get('AWS_SES_REGION_NAME', 'ap-northeast-1')
AWS_SES_REGION_ENDPOINT = os.environ.get('AWS_SES_REGION_ENDPOINT', 'email.ap-northeast-1.amazonaws.com')
AWS_SES_ACCESS_KEY_ID = os.environ.get('AWS_SES_ACCESS_KEY_ID')  # SES専用のアクセスキー
AWS_SES_SECRET_ACCESS_KEY = os.environ.get('AWS_SES_SECRET_ACCESS_KEY')  # SES専用のシークレットキー
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'VRC技術学術系Hub <info@vrc-ta-hub.com>')

# Admin email for notifications
ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', DEFAULT_FROM_EMAIL)

# Discord Webhook（管理者通知用）
DISCORD_WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK_URL', '')
DISCORD_REPORT_WEBHOOK_URL = os.environ.get('DISCORD_REPORT_WEBHOOK_URL', '')
