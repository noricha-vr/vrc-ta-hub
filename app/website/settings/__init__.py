"""website.settings パッケージ

責務別に分割した設定ファイルを順番にロードする。
- base: Django コア（INSTALLED_APPS / MIDDLEWARE / DATABASES / CACHES / LOGGING など）
- security: ALLOWED_HOSTS / CSRF / CORS / HSTS / DISCORD_AUTH_REQUIRED
- apis: Google / Gemini / SES / Discord Webhook
- storage: R2 / MEDIA / STORAGES
- caching: 将来の Redis / Session 拡張用スケルトン
- authentication: allauth / Discord OAuth / SOCIALACCOUNT_PROVIDERS

順序は base → security → apis → storage → caching → authentication で固定。
DEBUG / BASE_DIR などは base で定義され、後続モジュールは `from .base import ...`
で取り込む。
"""
from .base import *  # noqa: F401,F403
from .security import *  # noqa: F401,F403
from .apis import *  # noqa: F401,F403
from .storage import *  # noqa: F401,F403
from .caching import *  # noqa: F401,F403
from .authentication import *  # noqa: F401,F403
