"""scripts/create_news_post.py のテスト。

fixture の Markdown を読んで news.Post を作る汎用スクリプトの、
フロントマターのパース・作成・冪等・draft・カテゴリ不在を検証する。
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from news.models import Category, Post

# app/news/tests/ から見たリポジトリルート（コンテナでは / 、ローカルでは repo ルート）
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts"
SCRIPT_NAME = "create_news_post.py"
# 実運用で投入する fixture（本文はリポジトリ管理）
RELEASE_FIXTURE = "2026-09-17-presentation-list-redesign.md"


def _load_script():
    """scripts/ を sys.path に載せてスクリプトを import する。"""
    scripts_dir = str(SCRIPTS_DIR)
    inserted = scripts_dir not in sys.path
    if inserted:
        sys.path.insert(0, scripts_dir)
    try:
        spec = importlib.util.spec_from_file_location(
            "_create_news_post_under_test", SCRIPTS_DIR / SCRIPT_NAME
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if inserted:
            sys.path.remove(scripts_dir)


def _write_fixture(root: Path, name: str, content: str) -> None:
    """一時ディレクトリ配下に app_dir() 相当の fixture を置く。"""
    fixtures_dir = root / "news" / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    (fixtures_dir / name).write_text(content, encoding="utf-8")


SAMPLE_MARKDOWN = """---
title: テスト記事のタイトル
slug: test-news-post
category: update
meta_description: テスト用のメタディスクリプション
---

本文の1行目です。

## 見出し

本文の続き。
"""


class ParseFrontMatterTest(SimpleTestCase):
    """フロントマターのパース（Django DB 非依存）"""

    def setUp(self):
        self.module = _load_script()

    def test_parses_keys_and_body(self):
        """必須キー・任意キーと本文を分離する"""
        metadata, body = self.module.parse_front_matter(SAMPLE_MARKDOWN)

        self.assertEqual(metadata["title"], "テスト記事のタイトル")
        self.assertEqual(metadata["slug"], "test-news-post")
        self.assertEqual(metadata["category"], "update")
        self.assertEqual(metadata["meta_description"], "テスト用のメタディスクリプション")
        self.assertTrue(body.startswith("本文の1行目です。"))
        self.assertNotIn("---", body.splitlines()[0])

    def test_value_may_contain_colon(self):
        """値に含まれるコロンで分割されない"""
        text = SAMPLE_MARKDOWN.replace(
            "title: テスト記事のタイトル", "title: 告知: 新機能について"
        )
        metadata, _ = self.module.parse_front_matter(text)
        self.assertEqual(metadata["title"], "告知: 新機能について")

    def test_quoted_value_is_unquoted(self):
        """クォート付きの値は外して取り込む"""
        text = SAMPLE_MARKDOWN.replace(
            "slug: test-news-post", 'slug: "test-news-post"'
        )
        metadata, _ = self.module.parse_front_matter(text)
        self.assertEqual(metadata["slug"], "test-news-post")

    def test_missing_delimiter_raises(self):
        """開始区切りが無ければエラー"""
        with self.assertRaises(self.module.FrontMatterError):
            self.module.parse_front_matter("title: x\n\n本文")

    def test_missing_required_key_raises(self):
        """必須キー不足はエラー"""
        text = SAMPLE_MARKDOWN.replace("category: update\n", "")
        with self.assertRaises(self.module.FrontMatterError):
            self.module.parse_front_matter(text)

    def test_release_fixture_parses(self):
        """実際に投入する fixture が期待どおりパースできる"""
        path = self.module.app_dir() / "news" / "fixtures" / RELEASE_FIXTURE
        metadata, body = self.module.parse_front_matter(
            path.read_text(encoding="utf-8")
        )

        self.assertEqual(metadata["title"], "発表一覧をブログ形式にリニューアルしました")
        self.assertEqual(metadata["slug"], "2026-09-17-presentation-list-redesign")
        self.assertEqual(metadata["category"], "update")
        self.assertTrue(metadata["meta_description"])
        self.assertIn("## 主な変更内容", body)


class CreateNewsPostTest(TestCase):
    """create_news_post() の動作"""

    def setUp(self):
        self.module = _load_script()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        _write_fixture(self.root, "sample.md", SAMPLE_MARKDOWN)
        patcher = patch.object(self.module, "app_dir", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        Category.objects.get_or_create(
            slug="update", defaults={"name": "アップデート", "order": 0}
        )

    def test_creates_published_post(self):
        """既定では公開記事として作成する"""
        self.assertEqual(self.module.create_news_post("sample.md"), 0)

        post = Post.objects.get(slug="test-news-post")
        self.assertEqual(post.title, "テスト記事のタイトル")
        self.assertEqual(post.category.slug, "update")
        self.assertEqual(post.meta_description, "テスト用のメタディスクリプション")
        self.assertTrue(post.is_published)
        self.assertIsNotNone(post.published_at)
        self.assertTrue(post.body_markdown.startswith("本文の1行目です。"))

    def test_creates_draft_post(self):
        """--draft 相当では非公開・published_at なしで作成する"""
        self.assertEqual(self.module.create_news_post("sample.md", draft=True), 0)

        post = Post.objects.get(slug="test-news-post")
        self.assertFalse(post.is_published)
        self.assertIsNone(post.published_at)

    def test_is_idempotent(self):
        """同じ slug の記事があれば作らずに 0 を返す"""
        self.assertEqual(self.module.create_news_post("sample.md"), 0)
        self.assertEqual(self.module.create_news_post("sample.md"), 0)

        self.assertEqual(Post.objects.filter(slug="test-news-post").count(), 1)

    def test_missing_category_returns_1(self):
        """カテゴリ不在は exit 1"""
        Post.objects.all().delete()
        Category.objects.filter(slug="update").delete()

        self.assertEqual(self.module.create_news_post("sample.md"), 1)
        self.assertFalse(Post.objects.filter(slug="test-news-post").exists())

    def test_missing_fixture_returns_1(self):
        """fixture 不在は exit 1"""
        self.assertEqual(self.module.create_news_post("no-such-file.md"), 1)

    def test_path_traversal_returns_1(self):
        """fixtures ディレクトリ外の指定は exit 1"""
        self.assertEqual(self.module.create_news_post("../settings.py"), 1)

    def test_broken_front_matter_returns_1(self):
        """フロントマターが壊れていれば exit 1"""
        _write_fixture(self.root, "broken.md", "title: x\n\n本文だけ")

        self.assertEqual(self.module.create_news_post("broken.md"), 1)

    def test_main_creates_post(self):
        """main() が引数を解釈して作成し 0 を返す"""
        with patch.object(self.module, "setup_django"):
            exit_code = self.module.main(["--fixture", "sample.md"])

        self.assertEqual(exit_code, 0)
        self.assertTrue(Post.objects.filter(slug="test-news-post").exists())
