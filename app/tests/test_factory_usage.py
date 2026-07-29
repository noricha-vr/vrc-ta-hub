"""共有factoryへ移行中のcore model直接生成件数をratchetする。"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path
from typing import Iterator
from unittest import TestCase


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPOSITORY_ROOT / 'app'
FACTORY_MODULE_PATH = Path('app/tests/factories.py')
GENERATED_PATH_PARTS = frozenset({
    '__pycache__',
    '.mypy_cache',
    '.pytest_cache',
    '.ruff_cache',
    'migrations',
})
CORE_MODEL_NAMES = frozenset({
    'Community',
    'CustomUser',
    'Event',
    'EventDetail',
    'User',
})
DIRECT_MANAGER_METHODS = frozenset({'create', 'create_user'})
MAX_DIRECT_CREATES_BY_MODEL = {
    'Community': 258,
    'CustomUser': 119,
    'Event': 160,
    'EventDetail': 157,
    'User': 107,
}
MAX_DIRECT_CORE_CREATES = 801


def _is_test_source(path: Path) -> bool:
    relative_path = path.relative_to(REPOSITORY_ROOT)
    if relative_path == FACTORY_MODULE_PATH:
        return False
    if GENERATED_PATH_PARTS.intersection(relative_path.parts):
        return False
    return 'tests' in relative_path.parts or path.name == 'tests.py' or path.name.startswith('test_')


def _iter_test_sources() -> Iterator[Path]:
    """factory本体と生成物を除くrepo内Pythonテストソースを返す。"""
    return (path for path in APP_ROOT.rglob('*.py') if _is_test_source(path))


def _direct_core_create_identity(node: ast.AST) -> tuple[str, str] | None:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr not in DIRECT_MANAGER_METHODS:
        return None

    manager = node.func.value
    if not isinstance(manager, ast.Attribute) or manager.attr != 'objects':
        return None
    if not isinstance(manager.value, ast.Name) or manager.value.id not in CORE_MODEL_NAMES:
        return None
    return manager.value.id, node.func.attr


def _count_direct_core_creates() -> Counter[str]:
    counts: Counter[str] = Counter()
    for path in _iter_test_sources():
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            identity = _direct_core_create_identity(node)
            if identity is not None:
                counts[identity[0]] += 1
    return counts


class FactoryUsageRatchetTest(TestCase):
    """core model fixtureの直接生成負債が増えないことを検証する。"""

    def test_ast_matcher_identifies_only_core_model_manager_calls(self):
        """モデル名とmanager APIの両方が一致するcallだけを数える。"""
        source = """
Community.objects.create(name='counted')
User.objects.create_user(user_name='counted')
OtherModel.objects.create(name='ignored')
CommunityFactory.create(name='ignored')
"""
        identities = [
            identity
            for node in ast.walk(ast.parse(source))
            if (identity := _direct_core_create_identity(node)) is not None
        ]

        self.assertCountEqual(identities, [('Community', 'create'), ('User', 'create_user')])

    def test_direct_core_model_fixture_debt_does_not_increase(self):
        """共有factory外の直接生成は今回のpost値以下に保つ。"""
        counts = _count_direct_core_creates()

        for model_name, maximum in MAX_DIRECT_CREATES_BY_MODEL.items():
            with self.subTest(model=model_name):
                self.assertLessEqual(
                    counts[model_name],
                    maximum,
                    f'{model_name}.objects direct create debt increased: {counts[model_name]} > {maximum}',
                )
        self.assertLessEqual(
            counts.total(),
            MAX_DIRECT_CORE_CREATES,
            f'repo test direct core create debt increased: {counts.total()} > {MAX_DIRECT_CORE_CREATES}',
        )
