# Django 5.2 移行計画

## ステータス

**完了**（2026-06 / 3 PR 構成で完了）。

| 項目 | 値 |
|------|-----|
| 移行元 | Django 4.2.30 |
| 移行先 | Django 5.2.14 (LTS) |
| 関連 Issue | #321 (親) / #386 (PR2) / #387 (Wave 1) / #388 (PR3) |
| 関連 PR | #385 (PR1) / #389 (Wave 1) / #390 (PR2) / PR3 |
| 完了日 | 2026-06-02 |

## 経緯（3 PR 構成）

Django 4.2 のサポートが 2026-04 で終了するため、次期 LTS の 5.2 へ段階的に移行した。
「Django 5.2 一発切替」は変更面積が広すぎるため、Django 4.2 でも安全に動くものを先行マージし、最後に本体切替を行う構成にした。

| PR | 内容 | 状態 |
|---|---|---|
| #385 (PR1) | logout テンプレートを `<a>` から POST フォームへ変更 | マージ済み |
| Wave 1 (#389) | Django 5.x で削除予定 API (`force_text` / `ugettext` / `url()` / `is_ajax` 等) の使用箇所を一掃 | マージ済み |
| #390 (PR2) | `unique_together` → `Meta.constraints` + `UniqueConstraint` 移行 (community / event / analytics) | マージ済み |
| PR3 | Django 5.2 本体 + 依存パッケージ一括アップグレード | 本 PR |

## PR3 の変更内容

### 依存パッケージ更新

| パッケージ | 旧 | 新 |
|---|---|---|
| Django | 4.2.30 | **5.2.14** |
| django-storages | 1.11.1 | 1.14.6 |
| django-allauth[socialaccount] | 65.3.1 | 65.18.0 |
| djangorestframework | 3.15.2 | 3.16.1 |
| django-bootstrap5 | 24.2 | 25.3 |
| django_filter | 24.2 | 25.2 |
| drf-spectacular | 0.28.0 | 0.29.0 |
| django-cors-headers | 4.4.0 | 4.9.0 |
| django-ses | 4.3.1 | 4.7.2 |

### settings.py

- `FORMS_URLFIELD_ASSUME_HTTPS = True` を追加
  - Django 6.0 で `forms.URLField` のデフォルトスキームが `http` → `https` に変わる挙動を先取りする transitional setting
  - `models.URLField` から自動生成される formfield 全てに適用するため settings レベルで指定

### user_account/forms.py

- 明示的な `forms.URLField(...)` 4 箇所に `assume_scheme='https'` を追加
  - 該当: `group_url` / `organizer_url` / `sns_url` / `discord`

### allauth 設定の扱い

- `ACCOUNT_AUTHENTICATION_METHOD` / `ACCOUNT_EMAIL_REQUIRED` / `ACCOUNT_USERNAME_REQUIRED` の旧設定は **本 PR では触らない**
- django-allauth 65.4+ で backwards compatible のため旧設定のままで動作する
- 新設定（`ACCOUNT_LOGIN_METHODS` / `ACCOUNT_SIGNUP_FIELDS`）への書き換えは別 Issue で対応する
- 結果として `python manage.py check` で deprecation 警告 3 件 + `account.W001` 競合警告 1 件が残るが、動作影響なし

## 検証結果

- `docker compose exec vrc-ta-hub python manage.py check`: allauth 設定の deprecation 警告 4 件のみ（前項参照）
- `docker compose exec vrc-ta-hub python manage.py test`: **1549 tests OK** (skipped=13)
- `python -Wd manage.py test` で `RemovedInDjango60Warning`: `FORMS_URLFIELD_ASSUME_HTTPS` transitional setting の使用通知 1 件のみ（公式の正規ルート、6.0 時に削除）
- Playwright 実機検証（http://localhost:8015）: トップ / ログイン / イベント一覧 / `/api/v1/event/` / 集会一覧 / ログアウト POST / セッション破棄 全て成功

## 既知の残課題（別 Issue 対応）

- allauth 65.4+ の `ACCOUNT_LOGIN_METHODS` / `ACCOUNT_SIGNUP_FIELDS` 新設定への移行
  - 旧設定で動作はするが、deprecation 警告が継続する
  - `account.W001` (`ACCOUNT_LOGIN_METHODS conflicts with ACCOUNT_SIGNUP_FIELDS`) は新形式の自動推論で生じている内部競合

## 関連リンク

- Django 5.0 release notes: https://docs.djangoproject.com/en/5.0/releases/5.0/
- Django 5.1 release notes: https://docs.djangoproject.com/en/5.1/releases/5.1/
- Django 5.2 release notes: https://docs.djangoproject.com/en/5.2/releases/5.2/
- django-allauth changelog: https://docs.allauth.org/en/latest/release-notes/recent.html
