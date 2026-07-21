"""実サービスへ接続する live smoke テストの実行条件を定義する。"""

import os
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from django.test import tag


_TestTarget = TypeVar("_TestTarget", bound=Callable[..., object] | type)
_DUMMY_PREFIXES = ("dummy", "test", "changeme", "placeholder")


def _has_real_value(name: str) -> bool:
    value = (os.environ.get(name) or "").strip().lower()
    return bool(value) and not value.startswith(_DUMMY_PREFIXES)


def require_live_smoke(
    *required_env: str,
    required_files: tuple[str, ...] = (),
) -> Callable[[_TestTarget], _TestTarget]:
    """明示opt-inと実credentialが揃った実疎通テストだけを有効化する。"""

    enabled = os.environ.get("RUN_LIVE_SMOKE_TESTS") == "1"
    has_env = all(_has_real_value(name) for name in required_env)
    has_files = all(Path(path).is_file() for path in required_files)
    requirements = ", ".join((*required_env, *required_files)) or "required credentials"

    def decorator(target: _TestTarget) -> _TestTarget:
        tagged = tag("external_api", "live_smoke")(target)
        return unittest.skipUnless(
            enabled and has_env and has_files,
            f"live smoke requires RUN_LIVE_SMOKE_TESTS=1 and non-dummy {requirements}",
        )(tagged)

    return decorator
