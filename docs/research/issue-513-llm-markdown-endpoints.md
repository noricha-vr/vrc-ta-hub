# Issue #513 LLM 向け Markdown エンドポイント

## 目的

トップページの一次情報を、装飾的な HTML を解析せずに利用できる Markdown として提供する。対象はサイト案内の `/llms.txt` と、直近の発表・集会・特別企画を示す `/index.md` に限定する。

## 観測

`IndexView._build_database_context()` は VRChat 時刻の日付を含むキャッシュキーで、イベント・発表・特別企画をテンプレート向けの辞書に変換している。キャッシュ済みの値には DB クエリなしでアクセスできる一方、Vket 実績の画像とニュース記事は HTML 表示専用でキャッシュ外の処理である。

## 採用した実装

`IndexMarkdownView` は既存の `_build_database_context()` と `get_index_view_cache_key()` を利用し、HTML トップページと同一のキャッシュを共有する。共有済みのデータは Markdown 表示用にメモリ上で当日から7日間へ絞り、申請者由来の文字列は Markdown の構造にならないようエスケープする。Vket 実績・ニュースの処理は呼ばない。DB の `OperationalError` 時は、HTML トップページと同様に空の一覧を含む静的な Markdown を返す。

`/llms.txt` は [llms.txt 形式](https://llmstxt.org/) の H1、要約の blockquote、H2 のリンク一覧、`Optional` 節を使用し、詳細な一次情報への `/index.md` と既存 JSON API を案内する。

## 却下した代替案

- 同一 URL の Content Negotiation は、HTML 表示と分析・キャッシュを URL 単位で分けられず、既存 View への変更範囲も広がるため採用しない。
- 全詳細ページの Markdown 化と全文ファイルは、利用実績を確認する前の二重管理と大容量化を避けるため今回の範囲から外す。

## 検証方法

- Django テストで両エンドポイントの Markdown Content-Type、`llms.txt` の節、Markdown の一次情報、当週外の除外、Markdown 構文のエスケープ、DB 障害時の縮退、robots.txt の Allow 行を確認する。
- HTML を先に取得した後の Markdown 取得を `assertNumQueries(0)` で確認し、日付キャッシュが共有されることを検証する。
