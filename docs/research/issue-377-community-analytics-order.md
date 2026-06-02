# Issue 377: 集会詳細ページのアクセス解析カード表示順

## 調査対象

- `app/community/templates/community/detail.html`
- `app/community/views/public.py`
- `app/community/tests/test_analytics_section.py`
- `CLAUDE.md`

## 観測結果

- 集会詳細ページのアクセス解析カードは、テンプレート末尾で `can_view_analytics` により表示されていた。
- `can_view_analytics` は view 側で `user.is_superuser or community.is_manager(user)` として算出され、owner/staff/superuser のみが対象になる。
- 権限がない場合は `daily_series` と `source_breakdown` が context に入らないため、テンプレート表示条件だけに依存しない権限境界になっている。
- Chart.js の読み込みは `{% block extra_js %}` 側で `can_view_analytics` により制御されており、HTML セクションの移動とは独立している。

## 原因候補

アクセス解析カードの include 位置が superuser 向けの「集会主催者の連絡先」カードより後ろにあり、管理者向け情報の中でも下部へ押し出されていた。

## 改善案

Issue で推奨されていた案Aを採用し、アクセス解析カードを「集会主催者の連絡先」カードの直前へ移動する。

この方針は、既存の管理者向けカード群の並び順だけを変えるため、一般ユーザー向けのメイン情報や権限判定には影響しない。

## 却下した代替案

案Bとしてページ最上部への移動も考えられるが、集会の基本情報より上に管理者専用カードを置くと、詳細ページ全体の主情報より管理機能が先に表示される。今回の要望は「主催者カードより上」への移動であり、管理者向け情報の先頭へ移す案Aの方が変更範囲が小さい。

## 検証手順

- `docker compose exec vrc-ta-hub python manage.py test community.tests.test_analytics_section`
- superuser HTML で `id="analytics-section"` が「集会主催者の連絡先」より前に現れることをテストで確認する。
- 匿名ユーザー、他集会の owner/staff にはアクセス解析セクションが表示されない既存テストを継続して確認する。
