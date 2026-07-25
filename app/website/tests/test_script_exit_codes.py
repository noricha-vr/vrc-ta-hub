"""scripts/*.py の exit code 契約テスト。

cron / 手動実行のどちらでも失敗を検知できるよう、単発メンテナンススクリプトが
- `main()` の戻り値を `sys.exit()` に渡す（失敗時 exit 1）
- 進捗・診断を print ではなく logging に出す
- import しただけでは django.setup() が走らない（テスト可能性の担保）
ことを検証する。実処理は DB 状態に依存するため、代表的な失敗パスのみ
実際に import して戻り値を確認する。
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

# app/website/tests/ から見たリポジトリルート
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts"

# exit code 契約の対象スクリプト（Django の単発メンテナンス用）
TARGET_SCRIPTS = (
    "check_event_schedule.py",
    "create_activity_posts.py",
    "create_update_post.py",
    "create_vket_posts.py",
    "fix_h1_duplicates.py",
    "fix_inner_h1_tags.py",
)


def _parse(script_name: str) -> ast.Module:
    return ast.parse((SCRIPTS_DIR / script_name).read_text(encoding="utf-8"))


def _main_guard_body(tree: ast.Module) -> list[ast.stmt]:
    """`if __name__ == '__main__':` ブロックの本体を返す。"""
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "__name__"
        ):
            return node.body
    return []


class ScriptExitCodeContractTest(SimpleTestCase):
    """静的解析で exit code 契約を検証する。"""

    def test_scripts_exist(self):
        for name in TARGET_SCRIPTS:
            self.assertTrue((SCRIPTS_DIR / name).exists(), name)

    def test_main_guard_exits_with_main_return_value(self):
        """`sys.exit(main())` で main() の戻り値を exit code にすること。"""
        for name in TARGET_SCRIPTS:
            with self.subTest(script=name):
                body = _main_guard_body(_parse(name))
                self.assertTrue(body, f"{name}: __main__ ガードがない")

                exits = [
                    node
                    for node in ast.walk(ast.Module(body=body, type_ignores=[]))
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "exit"
                ]
                self.assertTrue(exits, f"{name}: sys.exit(...) がない")

                arg_sources = [
                    ast.dump(call.args[0]) for call in exits if call.args
                ]
                self.assertTrue(
                    any("'main'" in src for src in arg_sources),
                    f"{name}: sys.exit(main()) になっていない",
                )

    def test_scripts_define_main_function(self):
        for name in TARGET_SCRIPTS:
            with self.subTest(script=name):
                names = [
                    node.name
                    for node in _parse(name).body
                    if isinstance(node, ast.FunctionDef)
                ]
                self.assertIn("main", names, f"{name}: main() がない")

    def test_scripts_do_not_use_print(self):
        """診断出力は logging に統一する（print は stdout を汚す）。"""
        for name in TARGET_SCRIPTS:
            with self.subTest(script=name):
                prints = [
                    node
                    for node in ast.walk(_parse(name))
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "print"
                ]
                self.assertEqual(prints, [], f"{name}: print が残っている")

    def test_scripts_do_not_call_django_setup_at_import_time(self):
        """import 時の副作用禁止（テストから import できるようにする）。"""
        for name in TARGET_SCRIPTS:
            with self.subTest(script=name):
                calls = [
                    node
                    for node in _parse(name).body
                    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                ]
                self.assertEqual(
                    calls, [], f"{name}: モジュールトップレベルで関数を呼んでいる"
                )


def _load_script(script_name: str):
    """scripts/ を sys.path に載せてスクリプトを import する。"""
    scripts_dir = str(SCRIPTS_DIR)
    inserted = scripts_dir not in sys.path
    if inserted:
        sys.path.insert(0, scripts_dir)
    try:
        module_name = f"_vrc537_{Path(script_name).stem}"
        spec = importlib.util.spec_from_file_location(
            module_name, SCRIPTS_DIR / script_name
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if inserted:
            sys.path.remove(scripts_dir)


class ScriptFailurePathTest(TestCase):
    """代表的な失敗パスが exit code 1 を返すことを実際に確認する。"""

    def _delete_categories(self):
        """カテゴリ不在（= 前提条件が崩れた状態）を作る。"""
        from news.models import Category, Post

        # Post.category は PROTECT なので、記事を消してからカテゴリを消す
        Post.objects.all().delete()
        Category.objects.all().delete()

    def test_create_update_post_returns_1_when_category_missing(self):
        self._delete_categories()
        module = _load_script("create_update_post.py")
        self.assertEqual(module.create_update_post(), 1)

    def test_create_vket_posts_returns_1_when_category_missing(self):
        self._delete_categories()
        module = _load_script("create_vket_posts.py")
        self.assertEqual(module.create_vket_posts(), 1)

    def test_create_activity_posts_returns_1_when_category_missing(self):
        self._delete_categories()
        module = _load_script("create_activity_posts.py")
        self.assertEqual(module.create_activity_posts(), 1)

    def test_create_vket_posts_returns_1_when_fixture_missing(self):
        """fixture が見つからない場合も失敗として扱う。"""
        module = _load_script("create_vket_posts.py")
        with patch.object(module, "app_dir", return_value=Path("/nonexistent")):
            self.assertEqual(module.create_vket_posts(), 1)

    def test_create_update_post_returns_1_when_fixture_missing(self):
        module = _load_script("create_update_post.py")
        with patch.object(module, "app_dir", return_value=Path("/nonexistent")):
            self.assertEqual(module.create_update_post(), 1)

    def test_check_event_schedule_returns_0_without_duplicates(self):
        module = _load_script("check_event_schedule.py")
        self.assertEqual(module.check_event_schedule(), 0)

    def test_fix_h1_duplicates_returns_0_on_empty_db(self):
        module = _load_script("fix_h1_duplicates.py")
        self.assertEqual(module.fix_h1_duplicates(), 0)

    def test_fix_inner_h1_tags_returns_0_on_empty_db(self):
        module = _load_script("fix_inner_h1_tags.py")
        self.assertEqual(module.fix_inner_h1_tags(), 0)
