# Issue #500 Campaign UTM validator migration

## 調査結果

`analytics.Campaign` の `utm_source` と `utm_medium` は、モデルでは
`\A[A-Za-z0-9_.\-]+\Z` の validator を使用している。一方、これらの UTM
フィールドを最後に変更した migration
`0004_alter_campaign_utm_medium_alter_campaign_utm_source` には、以前の
`^[A-Za-z0-9_.\-]+$` が記録されたままだった。

CI と同じダミー環境変数を設定した使い捨てコンテナで
`python manage.py makemigrations analytics --dry-run --verbosity 3` を実行すると、
`0008_alter_campaign_utm_medium_alter_campaign_utm_source` が必要と報告された。

## 対応方針

生成結果どおりに 2 件の `AlterField` のみを追加する。カラム型、最大長、null 制約、
default、データ移行は変更しないため、既存レコードの変換や削除を伴わない。

## 検証

追加後に同じ使い捨てコンテナで
`python manage.py makemigrations --check --dry-run` を実行し、未生成 migration がないことを確認する。
