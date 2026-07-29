"""core model fixtureの直接生成をimport解決して集計する。"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path
from typing import Iterator, TypeAlias


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
CORE_MODEL_PATHS = {
    ('community', 'models', 'Community'): 'Community',
    ('event', 'models', 'Event'): 'Event',
    ('event', 'models', 'EventDetail'): 'EventDetail',
    ('user_account', 'models', 'CustomUser'): 'CustomUser',
    ('django', 'contrib', 'auth', 'models', 'User'): 'User',
}
GET_USER_MODEL_PATH = ('django', 'contrib', 'auth', 'get_user_model')
DIRECT_MANAGER_METHODS = frozenset({'create', 'create_user'})

Symbol: TypeAlias = tuple[str, str | tuple[str, ...]] | None
Scope: TypeAlias = tuple[str, dict[str, Symbol]]


def _is_test_source(path: Path, repository_root: Path) -> bool:
    relative_path = path.relative_to(repository_root)
    if relative_path == FACTORY_MODULE_PATH:
        return False
    if GENERATED_PATH_PARTS.intersection(relative_path.parts):
        return False
    return 'tests' in relative_path.parts or path.name == 'tests.py' or path.name.startswith('test_')


def _iter_test_sources(app_root: Path, repository_root: Path) -> Iterator[Path]:
    """factory本体と生成物を除くrepo内Pythonテストソースを返す。"""
    return (path for path in app_root.rglob('*.py') if _is_test_source(path, repository_root))


def _attribute_parts(node: ast.AST) -> tuple[str, ...] | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return tuple(reversed(parts))


def _target_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.List, ast.Tuple)):
        return {name for element in node.elts for name in _target_names(element)}
    return set()


def _argument_names(arguments: ast.arguments) -> set[str]:
    names = {
        argument.arg
        for argument in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
        )
    }
    if arguments.vararg:
        names.add(arguments.vararg.arg)
    if arguments.kwarg:
        names.add(arguments.kwarg.arg)
    return names


class _FunctionLocalCollector(ast.NodeVisitor):
    """関数全体でローカル束縛される名前を収集する。"""

    def __init__(self, node: ast.FunctionDef | ast.AsyncFunctionDef):
        self.names = _argument_names(node.args)
        self.global_names: set[str] = set()

        for statement in node.body:
            self.visit(statement)
        self.names.difference_update(self.global_names)

    def visit_Global(self, node: ast.Global):
        self.global_names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal):
        self.global_names.update(node.names)

    def visit_Name(self, node: ast.Name):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_Import(self, node: ast.Import):
        for imported in node.names:
            self.names.add(imported.asname or imported.name.split('.')[0])

    def visit_ImportFrom(self, node: ast.ImportFrom):
        for imported in node.names:
            if imported.name != '*':
                self.names.add(imported.asname or imported.name)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.names.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self.names.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef):
        self.names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda):
        return

    def visit_ListComp(self, node: ast.ListComp):
        return

    def visit_SetComp(self, node: ast.SetComp):
        return

    def visit_DictComp(self, node: ast.DictComp):
        return

    def visit_GeneratorExp(self, node: ast.GeneratorExp):
        return


class _CoreCreateVisitor(ast.NodeVisitor):
    """import解決済みのcore model manager callを収集する。"""

    def __init__(self, model_paths: dict[tuple[str, ...], str] | None = None):
        self.identities: list[tuple[str, str]] = []
        self.scopes: list[Scope] = [('module', {})]
        self.model_paths = model_paths or CORE_MODEL_PATHS
        self.model_modules = frozenset(path[:-1] for path in self.model_paths)

    def _lookup(self, name: str) -> Symbol:
        crossed_function = False
        for scope_kind, bindings in reversed(self.scopes):
            if scope_kind == 'function':
                crossed_function = True
            if scope_kind == 'class' and crossed_function:
                continue
            if name in bindings:
                return bindings[name]
        return None

    def _bind(self, name: str, symbol: Symbol):
        self.scopes[-1][1][name] = symbol

    def _resolve_qualified_path(self, node: ast.AST) -> tuple[str, ...] | None:
        parts = _attribute_parts(node)
        if not parts:
            return None
        symbol = self._lookup(parts[0])
        if symbol is None or symbol[0] != 'module':
            return None
        return (*symbol[1], *parts[1:])

    def _resolve_model(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            symbol = self._lookup(node.id)
            if symbol is not None and symbol[0] == 'model':
                return str(symbol[1])
            return None
        qualified_path = self._resolve_qualified_path(node)
        if qualified_path is None:
            return None
        return self.model_paths.get(qualified_path)

    def _is_get_user_model_call(self, node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        if isinstance(node.func, ast.Name):
            symbol = self._lookup(node.func.id)
            return symbol is not None and symbol[0] == 'get_user_model'
        return self._resolve_qualified_path(node.func) == GET_USER_MODEL_PATH

    def _assignment_symbol(self, target_name: str, value: ast.AST) -> Symbol:
        if self._is_get_user_model_call(value):
            model_name = target_name if target_name in {'CustomUser', 'User'} else 'User'
            return 'model', model_name
        resolved_model = self._resolve_model(value)
        if resolved_model is not None:
            return 'model', resolved_model
        return None

    def _bind_target(self, target: ast.AST, symbol: Symbol = None):
        target_names = _target_names(target)
        for target_name in target_names:
            self._bind(target_name, symbol if len(target_names) == 1 else None)

    def _symbol_for_path(self, path: tuple[str, ...]) -> Symbol:
        if path in self.model_paths:
            return 'model', self.model_paths[path]
        if path == GET_USER_MODEL_PATH:
            return 'get_user_model', 'get_user_model'
        if path in self.model_modules or any(
            model_path[:len(path)] == path for model_path in self.model_paths
        ) or GET_USER_MODEL_PATH[:len(path)] == path:
            return 'module', path
        return None

    def visit_Import(self, node: ast.Import):
        for imported in node.names:
            full_path = tuple(imported.name.split('.'))
            if imported.asname:
                self._bind(imported.asname, self._symbol_for_path(full_path))
            else:
                self._bind(full_path[0], self._symbol_for_path((full_path[0],)))

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.level:
            for imported in node.names:
                if imported.name != '*':
                    self._bind(imported.asname or imported.name, None)
            return
        module_path = tuple((node.module or '').split('.'))
        for imported in node.names:
            if imported.name == '*':
                continue
            self._bind(
                imported.asname or imported.name,
                self._symbol_for_path((*module_path, imported.name)),
            )

    def visit_Assign(self, node: ast.Assign):
        self.visit(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                self._bind(target.id, self._assignment_symbol(target.id, node.value))
            else:
                self._bind_target(target)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        if node.value is not None:
            self.visit(node.value)
        if isinstance(node.target, ast.Name) and node.value is not None:
            self._bind(node.target.id, self._assignment_symbol(node.target.id, node.value))
        else:
            self._bind_target(node.target)

    def visit_AugAssign(self, node: ast.AugAssign):
        self.visit(node.value)
        self._bind_target(node.target)

    def visit_NamedExpr(self, node: ast.NamedExpr):
        self.visit(node.value)
        if isinstance(node.target, ast.Name):
            self._bind(node.target.id, self._assignment_symbol(node.target.id, node.value))
        else:
            self._bind_target(node.target)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef):
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        if node.returns is not None:
            self.visit(node.returns)
        self._bind(node.name, None)

        local_names = _FunctionLocalCollector(node).names
        self.scopes.append(('function', dict.fromkeys(local_names)))
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._visit_function(node)

    def visit_Lambda(self, node: ast.Lambda):
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        self.scopes.append(('function', dict.fromkeys(_argument_names(node.args))))
        self.visit(node.body)
        self.scopes.pop()

    def visit_ClassDef(self, node: ast.ClassDef):
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        self._bind(node.name, None)

        self.scopes.append(('class', {}))
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()

    def visit_For(self, node: ast.For | ast.AsyncFor):
        self.visit(node.iter)
        self._bind_target(node.target)
        for statement in (*node.body, *node.orelse):
            self.visit(statement)

    def visit_AsyncFor(self, node: ast.AsyncFor):
        self.visit_For(node)

    def visit_With(self, node: ast.With | ast.AsyncWith):
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._bind_target(item.optional_vars)
        for statement in node.body:
            self.visit(statement)

    def visit_AsyncWith(self, node: ast.AsyncWith):
        self.visit_With(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler):
        if node.type is not None:
            self.visit(node.type)
        if node.name:
            self._bind(node.name, None)
        for statement in node.body:
            self.visit(statement)

    def _visit_comprehension(
        self,
        generators: list[ast.comprehension],
        result_nodes: tuple[ast.AST, ...],
    ):
        first_generator, *remaining_generators = generators
        self.visit(first_generator.iter)
        self.scopes.append(('function', {}))
        self._bind_target(first_generator.target)
        for condition in first_generator.ifs:
            self.visit(condition)

        for generator in remaining_generators:
            self.visit(generator.iter)
            self._bind_target(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        for result_node in result_nodes:
            self.visit(result_node)
        self.scopes.pop()

    def visit_ListComp(self, node: ast.ListComp):
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_SetComp(self, node: ast.SetComp):
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_DictComp(self, node: ast.DictComp):
        self._visit_comprehension(node.generators, (node.key, node.value))

    def visit_GeneratorExp(self, node: ast.GeneratorExp):
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_Call(self, node: ast.Call):
        if isinstance(node.func, ast.Attribute) and node.func.attr in DIRECT_MANAGER_METHODS:
            manager = node.func.value
            if isinstance(manager, ast.Attribute) and manager.attr == 'objects':
                model_name = self._resolve_model(manager.value)
                if model_name is not None:
                    self.identities.append((model_name, node.func.attr))
        self.generic_visit(node)


def find_direct_core_creates(
    tree: ast.AST,
    model_paths: dict[tuple[str, ...], str] | None = None,
) -> list[tuple[str, str]]:
    """ASTからimport解決できるcore model直接生成を返す。"""
    visitor = _CoreCreateVisitor(model_paths)
    visitor.visit(tree)
    return visitor.identities


def _module_path(path: Path, app_root: Path) -> tuple[str, ...]:
    relative_path = path.relative_to(app_root).with_suffix('')
    parts = relative_path.parts
    return parts[:-1] if parts[-1] == '__init__' else parts


def _discover_project_model_paths(
    app_root: Path,
    repository_root: Path,
) -> dict[tuple[str, ...], str]:
    """プロジェクト内moduleが再公開するcore model aliasを解決する。"""
    parsed_modules = {
        _module_path(path, app_root): ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for path in app_root.rglob('*.py')
        if not GENERATED_PATH_PARTS.intersection(path.relative_to(repository_root).parts)
    }
    model_paths = dict(CORE_MODEL_PATHS)

    changed = True
    while changed:
        changed = False
        for module_path, tree in parsed_modules.items():
            visitor = _CoreCreateVisitor(model_paths)
            visitor.visit(tree)
            for exported_name, symbol in visitor.scopes[0][1].items():
                if symbol is None or symbol[0] != 'model':
                    continue
                exported_path = (*module_path, exported_name)
                model_name = str(symbol[1])
                if model_paths.get(exported_path) == model_name:
                    continue
                model_paths[exported_path] = model_name
                changed = True
    return model_paths


def count_direct_core_creates(
    repository_root: Path = REPOSITORY_ROOT,
    app_root: Path = APP_ROOT,
) -> Counter[str]:
    """repo内テストのcore model直接生成をモデル別に集計する。"""
    counts: Counter[str] = Counter()
    model_paths = _discover_project_model_paths(app_root, repository_root)
    for path in _iter_test_sources(app_root, repository_root):
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for model_name, _manager_method in find_direct_core_creates(tree, model_paths):
            counts[model_name] += 1
    return counts
