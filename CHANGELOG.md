# Changelog

この変更履歴は Keep a Changelog 形式で記載しています。

## 2026-10-05

### Fixed

- トップページの特別企画バッジの右側に、Googleカレンダーへ予定を追加するボタンを追加しました。

## 2026-10-04

### Security

- HTML・画像処理、暗号・OAuth、非同期通信、SQL解析およびテスト依存を更新し、既知のセキュリティ修正を取り込みました。OAuth依存との互換性を保つため django-allauth も 65.19.7 へ更新しました。

## 2026-10-03

### Changed

- PDFテキスト抽出とサムネイル生成を資源制限付きworkerへ分離し、制限超過やtimeout時も既存の省略動作を維持するようにしました (#667)。

## 2026-10-02

### Security

- Django 5.2.17、Django REST Framework 3.17.2、Requests 2.33.0、urllib3 2.8.0 に更新し、公開済みのセキュリティ修正を取り込みました (#665)。

### Changed

- PDF読み取りライブラリをpypdf 6.19.0へ更新しました (#663)。
