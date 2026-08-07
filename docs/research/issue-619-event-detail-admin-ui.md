# Issue #619: イベント詳細の管理者向け UI 配置

## 調査対象

- `app/event/templates/event/detail.html`
- `app/event/tests/test_event_detail_template.py`
- `app/event/tests/test_event_detail_analytics.py`

## 観測結果

- 管理操作、発表者アカウント、LT 注意書きは `can_manage_event_detail` の配下にある。
- アクセス解析は `can_view_analytics` で表示し、応募者の編集権限とは分離されている。
- 変更前はアクセス解析が管理操作より先、LT 注意書きが発表者アカウントより後に表示されていた。
- 紐づけ済みの発表者アカウントでは、見出し・状態文・解除ボタンが縦に並んでいた。

## 原因候補

管理者向けの UI が機能追加ごとに近接位置へ挿入され、操作の流れに沿った表示順が維持されていなかった。

## 採用改善案

管理操作の後に LT 注意書き、発表者アカウント、アクセス解析を配置する。アクセス解析は `can_manage_event_detail` の外に残し、既存の `can_view_analytics` 権限境界を保つ。

紐づけ済み状態のみ Bootstrap の `d-flex flex-wrap align-items-center gap-2` で横並びにし、狭い画面では自然に折り返す。未紐づけ時の説明、招待フォーム、URL 欄は変更しない。

## 却下した代替案

アクセス解析を `can_manage_event_detail` の内側へ移す案は、応募者に集会全体の解析情報が見えるおそれがあるため却下した。

未紐づけ時の招待 UI も横並びにする案は、説明文と URL 欄の可読性を変える一方で Issue の対象外のため却下した。

## 検証手順

- worktree の `app/` を read-only で直接マウントした使い捨てコンテナで、`python manage.py test event.tests.test_event_detail_template event.tests.test_event_detail_analytics` を実行する。共有の起動中コンテナは使用しない。
- 既存の `event.tests.test_event_detail_analytics` で、応募者には `can_manage_event_detail` があっても解析が表示されないことを確認する。
- 管理者画面で操作群、LT 注意書き、発表者アカウント、アクセス解析の順に表示され、紐づけ済み行がモバイル幅で折り返せることを確認する。
