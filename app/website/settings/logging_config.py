"""ログ設定.

structlog の初期化と Django LOGGING 定義を担う。BASE_DIR / DEBUG は base から取り込む。
"""
import structlog

from .base import BASE_DIR, DEBUG

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
