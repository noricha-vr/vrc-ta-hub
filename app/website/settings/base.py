"""Django コア設定.

INSTALLED_APPS / MIDDLEWARE / TEMPLATES / DATABASES / CACHES / LOGGING / I18N /
STATIC など、Django プロジェクトの骨格を定義する。BASE_DIR / SECRET_KEY / DEBUG /
TESTING もここで定義し、他の設定モジュールは `from .base import ...` で参照する。
"""
import logging
import os
import sys
from pathlib import Path

import structlog

_settings_logger = logging.getLogger('django.settings')


def _mask(value: str, visible: int = 5) -> str:
    """設定値の先頭数文字のみ表示し、残りをマスクする"""
    if len(value) <= visible:
        return value
    return value[:visible] + '***'


def _split_csv_env(env_name: str) -> list[str]:
    """カンマ区切り環境変数を空要素なしで展開する"""
    value = os.environ.get(env_name, '')
    return [item.strip() for item in value.split(',') if item.strip()]


# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/4.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('SECRET_KEY')

# Fernet 暗号化用の対称鍵 (community.encrypted_fields.EncryptedTextField で使用)。
# SECRET_KEY とは別管理にすることで鍵ローテーション容易性と二重防御を確保する。
# 生成: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
FERNET_KEY = os.environ.get('FERNET_KEY', '')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get('DEBUG') == 'True'

# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',
    'ta_hub',
    'community',
    'event',
    'analytics',
    'sitemap',
    'user_account',
    'event_calendar',
    'twitter',
    'news',
    'guide',
    'vket',
    'django_bootstrap5',
    'api_v1',
    'django_filters',
    'rest_framework',
    'corsheaders',
    'drf_spectacular',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.discord',
]

SITE_ID = 1

REST_FRAMEWORK = {
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle'
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '100/minute',
        'user': '100/minute'
    },
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'VRC技術学術系Hub API',
    'DESCRIPTION': 'VRChat内で開催される技術・学術系イベントの情報を管理するAPI',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'SWAGGER_UI_SETTINGS': {
        'deepLinking': True,
        'persistAuthorization': True,
        'displayOperationId': True,
    },
    'COMPONENT_SPLIT_REQUEST': True,
    'AUTHENTICATION_WHITELIST': [
        'api_v1.authentication.APIKeyAuthentication',
    ],
}

MIDDLEWARE = [
    # Cloud Run preview host は raw Host のまま下流へ流さない。
    'website.middleware.CanonicalCloudRunHostMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'allauth.account.middleware.AccountMiddleware',
    'user_account.middleware.DiscordAuthRequiredMiddleware',
]

ROOT_URLCONF = 'website.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'ta_hub' / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'community.context_processors.active_community',
            ],
        },
    },
]

WSGI_APPLICATION = 'website.wsgi.application'

# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': os.environ.get('DB_NAME'),
        'USER': os.environ.get('DB_USER'),
        'PASSWORD': os.environ.get('DB_PASSWORD'),
        'HOST': os.environ.get('DB_HOST'),
        'OPTIONS': {
            'charset': 'utf8mb4',
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
        },
    },
}

TESTING = os.environ.get('TESTING', '').strip().lower() in {'1', 'true', 'yes', 'on'}

if 'test' in sys.argv or TESTING:
    DATABASES['default'] = {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
    # PBKDF2 デフォルトは 600k iterations あり、create_user / client.login が多いテストで支配的になる
    PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

    # テスト環境で FERNET_KEY が未設定の場合は起動時に動的生成する。
    # CI (.github/workflows/ci.yml) や新規開発者の `manage.py test` でも
    # community.encrypted_fields を呼ぶテストが落ちないようにする防御層。
    # 本番では環境変数 FERNET_KEY が必須で、TESTING=1 にはならないため安全。
    # 固定鍵を埋め込むと secret スキャナの誤検知になるため都度生成する
    # (テスト間で値が変わるが、暗号化テストは override_settings で明示鍵を使う)。
    if not FERNET_KEY:
        from cryptography.fernet import Fernet as _Fernet
        FERNET_KEY = _Fernet.generate_key().decode()

_settings_logger.info('DB_NAME: %s', _mask(DATABASES['default']['NAME']))

# Cache settings
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'unique-snowflake',
    }
}

# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators

FILE_UPLOAD_MAX_MEMORY_SIZE = 30 * 1024 * 1024  # 30MB

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
# https://docs.djangoproject.com/en/4.2/topics/i18n/

LANGUAGE_CODE = 'ja-jp'

TIME_ZONE = 'Asia/Tokyo'

USE_I18N = True

USE_TZ = True

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.2/howto/static-files/

STATIC_URL = 'static/'

# Default primary key field type
# https://docs.djangoproject.com/en/4.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
STATIC_ROOT = os.path.join(BASE_DIR, 'static')  # ローカルに静的ファイルを収集するディレクトリ

# Django 6.0 で forms.URLField のデフォルトスキームが 'http' → 'https' になる挙動を先取り
# models.URLField から自動生成される formfield 全てに適用するため transitional setting を使う
FORMS_URLFIELD_ASSUME_HTTPS = True

LOGIN_URL = '/account/login/'
LOGIN_REDIRECT_URL = '/event/my_list/'

# ログディレクトリの設定
LOG_DIR = BASE_DIR / 'logs'
if DEBUG:
    # デバッグモード時はログディレクトリを作成
    LOG_DIR.mkdir(exist_ok=True)

# --- structlog 設定 -----------------------------------------------------------
# 本番 (DEBUG=False) は JSON 形式で GCP Cloud Logging に取り込みやすくし、
# 開発 (DEBUG=True) は ConsoleRenderer で人間可読にする。
# stdlib logging と統合するため ProcessorFormatter を併用しており、既存の
# `logger.info("...")` / `logger.error("...")` 形式は無変更で動作する。
_structlog_renderer = (
    structlog.dev.ConsoleRenderer(colors=False)
    if DEBUG
    else structlog.processors.JSONRenderer()
)

# stdlib LogRecord (logger.info(...) 経由) を structlog のパイプラインに通すための
# pre-chain。structlog ネイティブの bind_contextvars と TimeStamper を共通化する。
_foreign_pre_chain = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.ExtraAdder(),
    structlog.processors.TimeStamper(fmt="iso", utc=False),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
]

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=False),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        # ProcessorFormatter に最終レンダリングを委譲する。これにより stdlib と
        # structlog ネイティブの両方が同じ出力フォーマットに揃う。
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        # 構造化ログ統一フォーマッタ。stdlib logger 経由のレコードは
        # foreign_pre_chain を通って同じレンダラーに集約される。
        'structured': {
            '()': 'structlog.stdlib.ProcessorFormatter',
            'processor': _structlog_renderer,
            'foreign_pre_chain': _foreign_pre_chain,
        },
        # 既存の名前を残し、structured と同等の挙動にする (後方互換用)。
        'verbose': {
            '()': 'structlog.stdlib.ProcessorFormatter',
            'processor': _structlog_renderer,
            'foreign_pre_chain': _foreign_pre_chain,
        },
        'simple': {
            '()': 'structlog.stdlib.ProcessorFormatter',
            'processor': _structlog_renderer,
            'foreign_pre_chain': _foreign_pre_chain,
        },
    },
    'handlers': {
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'structured',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': True,
        },
        'allauth': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': True,
        },
        'event_detail': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': True,
        },
        'event': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': True,
        },
        'ta_hub': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': True,
        },
        'website': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': True,
        },
        'api_v1': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': True,
        },
        'community': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': True,
        },
        'analytics': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': True,
        },
        'account': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': True,
        },
        'sitemap': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': True,
        },
        'event_calendar': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': True,
        },
        'twitter': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': True,
        },
        'user_account': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': True,
        },
    },
}

# デバッグモード時はファイルハンドラーを追加
if DEBUG:
    LOGGING['handlers']['file'] = {
        'level': 'DEBUG',
        'class': 'logging.handlers.RotatingFileHandler',
        'filename': LOG_DIR / 'django.log',
        'maxBytes': 1024 * 1024 * 10,  # 10MB
        'backupCount': 5,
        'formatter': 'structured',
    }
    # 各ロガーにファイルハンドラーを追加
    for logger_name in LOGGING['loggers']:
        LOGGING['loggers'][logger_name]['handlers'].append('file')
        LOGGING['loggers'][logger_name]['level'] = 'DEBUG'

# --- Sentry エラートラッキング (本番のみ) -----------------------------------
# SENTRY_DSN が空、TESTING、DEBUG の場合は初期化しない。
# silent exception (logger.exception で吸われる例外) を ERROR イベントとして
# Sentry に転送し、本番での「黙って失敗する」状況を可視化する。
SENTRY_DSN = os.environ.get('SENTRY_DSN', '')


def _sentry_before_send(event, hint):  # pragma: no cover - 本番のみ呼ばれる
    """Sentry 送信前フィルタ.

    LoggingIntegration は ERROR 以上のログを全て event 化するため、生成 JSON や
    外部 API レスポンス body 等が混ざる既存 ERROR ログまで Sentry に流入する。
    まず Django の unhandled exception (logger 名 `django.*`) は無条件で通し、
    アプリ層の logger.error/exception は logentry に `is_silent=True` がある場合
    と `silent_failure` イベントのみ通す。それ以外は drop。
    """
    logger_name = event.get('logger') or ''
    if logger_name.startswith('django'):
        return event

    logentry = event.get('logentry') or {}
    message = logentry.get('message') or event.get('message') or ''
    if message == 'silent_failure':
        return event

    # `extra` 経由でセットした構造化フィールドを参照する。
    # sentry_sdk は LogRecord.is_silent を event['extra'] に伝搬する。
    extra = event.get('extra') or {}
    if extra.get('is_silent') is True:
        return event

    # 不明な ERROR ログはドロップ (PII / 生成物リーク防止)
    return None


if SENTRY_DSN and not TESTING and not DEBUG:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[
            DjangoIntegration(),
            # INFO 以上を breadcrumb として収集し、ERROR 以上をイベント送信する。
            # before_send で silent_failure / Django 例外のみ通すフィルタを掛ける。
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        send_default_pii=False,  # ユーザー情報 (IP / Cookie 等) は送らない
        traces_sample_rate=0.0,  # APM は無効。エラートラッキングのみ使う
        environment=os.environ.get('SENTRY_ENVIRONMENT', 'production'),
        before_send=_sentry_before_send,
    )
    _settings_logger.info('Sentry initialized')
