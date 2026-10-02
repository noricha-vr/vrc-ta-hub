# Changelog

この変更履歴は Keep a Changelog 形式で記載しています。

## 2026-10-03

### Changed

- PDFテキスト抽出とサムネイル生成を資源制限付きworkerへ分離し、制限超過やtimeout時も既存の省略動作を維持するようにしました (#667)。

## 2026-10-02

### Security

- Django 5.2.17、Django REST Framework 3.17.2、Requests 2.33.0、urllib3 2.8.0 に更新し、公開済みのセキュリティ修正を取り込みました (#665)。

### Changed

- PDF読み取りライブラリをpypdf 6.19.0へ更新しました (#663)。
