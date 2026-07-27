"""ad-hoc スクリプト用の Django セットアップと logging 初期化。

各スクリプトが同じ bootstrap を共有することで、
「import 時に django.setup() が走ってテストから import できない」問題を避ける。
setup_django() は main() から呼ぶ前提で、import 時には副作用を持たせない。
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# コンテナでは /app に app/ がマウントされる。ローカル実行では repo/app を使う。
DOCKER_APP_DIR = Path("/app")
LOCAL_APP_DIR = Path(__file__).resolve().parent.parent / "app"
DEFAULT_SETTINGS_MODULE = "website.settings"


def app_dir() -> Path:
    """Django プロジェクトルート（manage.py のあるディレクトリ）を返す。"""
    if (DOCKER_APP_DIR / "manage.py").exists():
        return DOCKER_APP_DIR
    return LOCAL_APP_DIR


def setup_django(settings_module: str = DEFAULT_SETTINGS_MODULE) -> None:
    """logging を初期化し Django をセットアップする。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    target = str(app_dir())
    if target not in sys.path:
        sys.path.insert(0, target)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", settings_module)

    import django

    django.setup()
