# Issue 372: 発表一覧の詳細リンクラベル変更

## 調査対象

- 対象ページ: `app/event/templates/event/my_list.html`
- 対象要素: 発表カード内の詳細リンク
- 対象リンク: `{% url 'event:detail' detail.pk %}`

## 観測結果

- 発表カードの操作ボタン群では、先頭の primary ボタンが発表詳細ページへ遷移していた。
- リンク先は `event:detail` のままで、発表詳細として自動生成された記事ページを表示する導線だった。
- 隣接する「編集」「削除」ボタンは管理操作であり、表示ラベル以外のレイアウトやスタイル変更は不要だった。

## 改善案

詳細リンクのボタンラベルを「確認」から「記事」に変更する。
リンク先と CSS クラスは変更せず、遷移先が記事ページであることだけを UI 文言で明確にする。

## 検証手順

1. `rg -n "btn-primary btn-sm\">記事|btn-primary btn-sm\">確認|event:detail" app/event/templates/event/my_list.html`
2. `docker compose run --rm --no-deps -T vrc-ta-hub python manage.py check`

## 検証結果

- `rg -n "btn-primary btn-sm\">記事" app/event/templates/event/my_list.html`: 1 件一致。
- `rg -n "btn-primary btn-sm\">確認" app/event/templates/event/my_list.html`: 一致なし。
- `git diff --check`: pass。
- `docker compose run --rm --no-deps -T vrc-ta-hub python manage.py check`: Docker イメージ内に `google.analytics` がなく、`ModuleNotFoundError` で fail。
- `uv pip install -r requirements.txt`: macOS 環境で `pysqlite3==0.5.3` のビルドに失敗し、ローカル `.venv` での `manage.py check` は未実行。

## 判断

テンプレート文言のみの変更で要件を満たせるため、ビュー、URL、モデル、スタイルには手を入れない。
ロジック変更がないため、追加の単体テストは作成しない。
