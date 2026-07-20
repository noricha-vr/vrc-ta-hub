"""外部API・メール送信の設定.

Google Calendar / YouTube / Gemini / GA4 / OpenRouter / xAI / Amazon SES /
Discord Webhook の認証情報・モデル名・宛先などをまとめる。
"""
import os

from .base import DEBUG, _mask, _settings_logger


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        _settings_logger.warning('%s is invalid; using %s', name, default)
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        _settings_logger.warning('%s is invalid; using %s', name, default)
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


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

# xAI Responses API + X Search — 集会活動監視
# キー未設定でも通常のWebアプリは起動し、活動監視エンドポイントだけ503を返す。
XAI_API_KEY = os.environ.get('XAI_API_KEY', '')
XAI_ACTIVITY_MODEL = os.environ.get('XAI_ACTIVITY_MODEL', 'grok-4.5')
XAI_ACTIVITY_TIMEOUT_SECONDS = _env_int('XAI_ACTIVITY_TIMEOUT_SECONDS', 180)
COMMUNITY_ACTIVITY_LOOKBACK_DAYS = _env_int('COMMUNITY_ACTIVITY_LOOKBACK_DAYS', 90)
COMMUNITY_ACTIVITY_CHECK_INTERVAL_DAYS = _env_int('COMMUNITY_ACTIVITY_CHECK_INTERVAL_DAYS', 7)
COMMUNITY_ACTIVITY_WARNING_DAYS = _env_int('COMMUNITY_ACTIVITY_WARNING_DAYS', 14)
COMMUNITY_ACTIVITY_REQUIRED_INACTIVE_CHECKS = _env_int(
    'COMMUNITY_ACTIVITY_REQUIRED_INACTIVE_CHECKS',
    2,
)
COMMUNITY_ACTIVITY_MIN_CONFIDENCE = _env_float('COMMUNITY_ACTIVITY_MIN_CONFIDENCE', 0.85)
COMMUNITY_ACTIVITY_BATCH_SIZE = _env_int('COMMUNITY_ACTIVITY_BATCH_SIZE', 5)
COMMUNITY_ACTIVITY_AUTO_HIDE = _env_bool('COMMUNITY_ACTIVITY_AUTO_HIDE', False)
COMMUNITY_ACTIVITY_DISCORD_WEBHOOK_URL = os.environ.get(
    'COMMUNITY_ACTIVITY_DISCORD_WEBHOOK_URL',
    '',
)
_settings_logger.info(
    'Community activity monitor: model=%s auto_hide=%s batch_size=%s',
    XAI_ACTIVITY_MODEL,
    COMMUNITY_ACTIVITY_AUTO_HIDE,
    COMMUNITY_ACTIVITY_BATCH_SIZE,
)

# GA4 Data API（ページ別アクセス解析）
# GA4_PROPERTY_ID は Data API 用の数値ID。base.html の measurement ID G-6BN9EHVMRW とは別物
GA4_PROPERTY_ID = os.environ.get('GA4_PROPERTY_ID', '444114283')
GOOGLE_APPLICATION_CREDENTIALS = os.getenv(
    'GOOGLE_APPLICATION_CREDENTIALS', '/app/secret/credentials.json'
)

REQUEST_TOKEN = os.environ.get('REQUEST_TOKEN')
assert REQUEST_TOKEN is not None, 'Please set REQUEST_TOKEN'

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
