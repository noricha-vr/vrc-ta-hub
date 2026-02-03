"""ガイドページのビュー

主催者・スタッフ向けの使い方マニュアルを表示するビュー。
docs/guide/ 配下のマークダウンファイルを読み込んで表示する。
"""
import logging
import re
from pathlib import Path
from typing import Optional

import yaml
from django.conf import settings
from django.http import Http404
from django.views.generic import TemplateView

from event.libs import convert_markdown

logger = logging.getLogger(__name__)

# ガイドのルートディレクトリ
# Docker環境では /docs/guide、ローカルではsettings.BASE_DIR.parent / 'docs' / 'guide'
GUIDE_ROOT = Path('/docs/guide') if Path('/docs/guide').exists() else settings.BASE_DIR.parent / 'docs' / 'guide'

class NavItem:
    """ナビゲーションアイテムを表すクラス"""

    def __init__(self, title: str, path: Optional[str] = None,
                 children: Optional[list] = None, owner_only: bool = False):
        self.title = title
        self.path = path
        self.children = children or []
        self.owner_only = owner_only

    @property
    def url(self) -> Optional[str]:
        """ガイドページのURLを返す"""
        if self.path:
            return f'/guide/{self.path}/'
        return None

    @property
    def has_children(self) -> bool:
        """子ナビゲーションを持つかどうか"""
        return len(self.children) > 0

    def __repr__(self):
        return f"NavItem(title={self.title}, path={self.path}, children={len(self.children)})"


def load_navigation() -> list[NavItem]:
    """ナビゲーション構造をYAMLファイルから読み込む

    Returns:
        NavItemのリスト
    """
    nav_file = GUIDE_ROOT / '_nav.yaml'

    if not nav_file.exists():
        logger.warning(f"ナビゲーションファイルが見つかりません: {nav_file}")
        return []

    try:
        with open(nav_file, 'r', encoding='utf-8') as f:
            nav_data = yaml.safe_load(f)

        return _parse_nav_items(nav_data)
    except Exception as e:
        logger.error(f"ナビゲーションファイルの読み込みに失敗: {e}")
        return []


def _parse_nav_items(items: list) -> list[NavItem]:
    """YAMLデータをNavItemリストに変換する

    Args:
        items: YAMLから読み込んだリストデータ

    Returns:
        NavItemのリスト
    """
    result = []
    for item in items:
        children = []
        if 'children' in item:
            children = _parse_nav_items(item['children'])

        nav_item = NavItem(
            title=item.get('title', ''),
            path=item.get('path'),
            children=children,
            owner_only=item.get('owner_only', False)
        )
        result.append(nav_item)

    return result


def get_flat_nav_items(nav_items: list[NavItem]) -> list[NavItem]:
    """ナビゲーションアイテムをフラットなリストに変換する

    前後のページナビゲーション用に使用する。

    Args:
        nav_items: ナビゲーションアイテムのリスト

    Returns:
        フラット化されたNavItemのリスト（pathを持つもののみ）
    """
    result = []
    for item in nav_items:
        if item.path:
            result.append(item)
        if item.children:
            result.extend(get_flat_nav_items(item.children))
    return result


def find_adjacent_pages(nav_items: list[NavItem], current_path: str) -> tuple[Optional[NavItem], Optional[NavItem]]:
    """現在のページの前後のページを取得する

    Args:
        nav_items: ナビゲーションアイテムのリスト
        current_path: 現在のページのパス

    Returns:
        (前のページ, 次のページ) のタプル
    """
    flat_items = get_flat_nav_items(nav_items)

    for i, item in enumerate(flat_items):
        if item.path == current_path:
            prev_item = flat_items[i - 1] if i > 0 else None
            next_item = flat_items[i + 1] if i < len(flat_items) - 1 else None
            return prev_item, next_item

    return None, None


def get_breadcrumbs(nav_items: list[NavItem], current_path: str) -> list[dict]:
    """パンくずリストを生成する

    Args:
        nav_items: ナビゲーションアイテムのリスト
        current_path: 現在のページのパス

    Returns:
        パンくずリスト（辞書のリスト）
    """
    breadcrumbs = [
        {'title': 'ホーム', 'url': '/'},
        {'title': '使い方ガイド', 'url': '/guide/'},
    ]

    # 現在のパスに対応するナビゲーションを探す
    def find_in_nav(items: list[NavItem], path: str, parent_title: Optional[str] = None) -> Optional[list[dict]]:
        for item in items:
            if item.path == path:
                result = []
                if parent_title:
                    result.append({'title': parent_title, 'url': None})
                result.append({'title': item.title, 'url': None})
                return result

            if item.children:
                found = find_in_nav(item.children, path, item.title)
                if found:
                    return found
        return None

    found_crumbs = find_in_nav(nav_items, current_path)
    if found_crumbs:
        breadcrumbs.extend(found_crumbs)

    return breadcrumbs


def load_markdown_content(path: str) -> tuple[str, dict]:
    """マークダウンファイルを読み込んでHTMLに変換する

    Args:
        path: ガイドのパス（例: 'community/create'）

    Returns:
        (HTML本文, フロントマター辞書) のタプル

    Raises:
        Http404: ファイルが見つからない場合
    """
    # パスの正規化とパストラバーサル対策
    safe_path = path.strip('/')
    md_file = (GUIDE_ROOT / f'{safe_path}.md').resolve()

    # GUIDE_ROOT外へのアクセスを防止
    if not md_file.is_relative_to(GUIDE_ROOT.resolve()):
        logger.warning(f"パストラバーサル攻撃を検出: {path}")
        raise Http404(f"ガイドページが見つかりません: {path}")

    if not md_file.exists():
        logger.warning(f"マークダウンファイルが見つかりません: {md_file}")
        raise Http404(f"ガイドページが見つかりません: {path}")

    try:
        with open(md_file, 'r', encoding='utf-8') as f:
            content = f.read()

        # フロントマターの抽出
        frontmatter = {}
        body = content

        if content.startswith('---'):
            match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)$', content, re.DOTALL)
            if match:
                frontmatter_str, body = match.groups()
                try:
                    frontmatter = yaml.safe_load(frontmatter_str) or {}
                except yaml.YAMLError as e:
                    logger.warning(f"フロントマターの解析に失敗: {e}")

        # マークダウンをHTMLに変換
        html = convert_markdown(body)

        return html, frontmatter

    except Exception as e:
        logger.error(f"マークダウンファイルの読み込みに失敗: {e}")
        raise Http404(f"ガイドページの読み込みに失敗しました: {path}")


class GuideIndexView(TemplateView):
    """ガイドのインデックスページ"""

    template_name = 'guide/index.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        nav_items = load_navigation()
        context['nav_items'] = nav_items
        context['breadcrumbs'] = [
            {'title': 'ホーム', 'url': '/'},
            {'title': '使い方ガイド', 'url': None},
        ]

        return context


class GuidePageView(TemplateView):
    """個別のガイドページを表示するビュー"""

    template_name = 'guide/page.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # パスの取得
        path = kwargs.get('path', 'index')

        # マークダウンの読み込み
        content, frontmatter = load_markdown_content(path)

        # ナビゲーションの読み込み
        nav_items = load_navigation()

        # 前後のページ
        prev_page, next_page = find_adjacent_pages(nav_items, path)

        # パンくずリスト
        breadcrumbs = get_breadcrumbs(nav_items, path)

        context.update({
            'content': content,
            'frontmatter': frontmatter,
            'page_title': frontmatter.get('title', 'ガイド'),
            'page_description': frontmatter.get('description', ''),
            'nav_items': nav_items,
            'current_path': path,
            'prev_page': prev_page,
            'next_page': next_page,
            'breadcrumbs': breadcrumbs,
        })

        return context
