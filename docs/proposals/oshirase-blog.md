# お知らせ（ブログ）機能 提案書

## 概要
- **目的**: グローバルナビの「運営情報」を「お知らせ」に置き換え、管理者が簡易ブログとして記事を投稿・公開できる仕組みを提供する。
- **対象ユーザー**: サイト利用者（閲覧）、管理者（投稿・管理）
- **到達イメージ**: シンプルなブログページ。ヘッダーにカテゴリー絞り込みボタンを配置。「お知らせ」を開くと全記事一覧が表示され、カテゴリークリックで該当カテゴリーの一覧ページに遷移。

## 要件
- **投稿機能**
  - 管理者のみが記事を作成・編集・公開できる（Django Admin で可）
  - 記事の要素: タイトル、本文（Markdown 可）、サムネイル（任意）、作成日時、更新日時、公開フラグ、カテゴリ、スラッグ
  - 文字装飾: Markdown → HTML 変換（既存の `markdown` / `bleach` を利用）

- **カテゴリ**
  - 固定カテゴリ（初期値）: 「お知らせ」「アップデート」
  - 将来的にカテゴリの追加が可能なデザイン（`Category` モデル化）

- **フロント機能**
  - 一覧ページ: `/news/` で全記事の新しい順リスト表示
  - カテゴリ別一覧: `/news/category/<slug>/` で対象カテゴリ記事のみ表示
  - ページ上部にカテゴリボタン群（全件/カテゴリ）を配置し、クリックでカテゴリ別一覧へ遷移
  - 記事詳細ページ: `/news/<slug>/`

- **ナビゲーション**
  - グローバルナビ: 「運営情報」を削除し「お知らせ」を追加（ログイン状況に関係なく表示）

- **API（任意・将来拡張）**
  - `api/v1/news/`（一覧/カテゴリ/詳細）

## 画面仕様（簡易）
- 一覧ページ（`/news/`）
  - ヘッダー: 見出し + カテゴリボタン（全件/お知らせ/アップデート）
  - リスト: カード形式（タイトル、抜粋、カテゴリバッジ、投稿日、サムネイル）
  - ページネーション
- カテゴリ一覧（`/news/category/<slug>/`）
  - ヘッダー: 選択カテゴリをアクティブ表示
  - リスト/ページネーションは一覧と同様
- 詳細ページ（`/news/<slug>/`）
  - タイトル、投稿日、カテゴリ、本文、（任意で）関連記事

## モデル設計（案）
```python
class Category(models.Model):
    name = models.CharField(max_length=50, unique=True)
    slug = models.SlugField(max_length=60, unique=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]

class Post(models.Model):
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    body_markdown = models.TextField()
    body_html = models.TextField(blank=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="posts")
    thumbnail = models.ImageField(upload_to="news/", null=True, blank=True)
    is_published = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]
```

- 保存時に `body_markdown` → `body_html` へサニタイズ済み HTML を生成。
- 既存の `bleach` 設定を再利用し安全なタグのみ許可。

## URL/ビュー設計（案）
- `news:list` → `/news/`
- `news:category` → `/news/category/<slug>/`
- `news:detail` → `/news/<slug>/`

- 一覧/カテゴリは同一テンプレートで対応し、カテゴリのフィルタリングだけ切替。
- 詳細は Markdown を HTML 化して出力。

## テンプレート構成（案）
- `templates/news/list.html`
  - 上部にカテゴリボタン（「全件」「お知らせ」「アップデート」）
  - 記事カード一覧 + ページネーション
- `templates/news/detail.html`
  - 記事本文表示（`safe`）

## 管理画面（Django Admin）
- `PostAdmin` で以下を設定:
  - リスト表示: タイトル、カテゴリ、公開状態、公開日時、作成日時
  - 検索: タイトル、本文
  - フィルタ: カテゴリ、公開状態
  - スラッグ自動生成: `prepopulated_fields = {"slug": ("title",)}`

## 権限制御
- 投稿/編集は管理者（`is_staff`）のみ
- 公開フラグ `True` のみ一般公開

## マイグレーション/初期データ
- マイグレーション作成
- 初期カテゴリ: 「お知らせ」「アップデート」（`fixtures` または `data migration`）

## ナビゲーション変更
- `base.html`
  - 「運営情報」を削除
  - 「お知らせ」メニューを追加し `/news/` へリンク

## デザイン指針
- 既存 Bootstrap 5.3 を踏襲。
- カテゴリボタン: `btn-outline-primary` を基本に、選択状態は `btn-primary`。

## 実装ステップ（概要）
1. アプリ `news` を作成、モデル `Category`, `Post` を実装
2. Markdown→HTML 変換 + sanitize 実装（`save()` または `signals`）
3. URL, View（一覧/カテゴリ/詳細）とテンプレート作成
4. 管理画面を整備しカテゴリ初期値投入
5. グローバルナビを「お知らせ」に更新
6. テスト（モデル、ビュー、テンプレート、URL 逆引き）

## 参考
- 既存技術スタック: Django / Bootstrap / bleach / markdown
- 既存 `api_v1` 構造を参照すれば API 拡張も容易
