# localhost-8015 操作マニュアル

## 基本情報

- URL: http://localhost:8015
- 種類: VRC技術学術ハブ（Django 4.2 / Python 3.12）
- 最終更新: 2026-03-02

## ログイン方法

### 認証情報の場所
- プロジェクト `.env.local` の `TEST_USER_NAME` / `TEST_USER_PASSWORD`（一般ユーザー）
- 管理者: `nori` / `teranori@gmail.com`（パスワードは別途確認）
- Discord OAuth 経由でもログイン可能（`/accounts/discord/login/`）

### 一般ユーザーログイン手順
1. http://localhost:8015/accounts/login/ にアクセス
2. ユーザー名: `.env.local` の `TEST_USER_NAME` の値
3. パスワード: `.env.local` の `TEST_USER_PASSWORD` の値
4. ログインボタンをクリック

### 要素の特定方法
- ユーザー名フィールド: `input[name="login"]`
- パスワードフィールド: `input[name="password"]`
- ログインボタン: `button[type="submit"]`

## ログイン自動化レシピ

### recipe_id: login.default
- role: user（一般ユーザー）
- version: 1
- login_url: http://localhost:8015/accounts/login/
- required_env:
  - TEST_USER_NAME（.env.local）
  - TEST_USER_PASSWORD（.env.local）
- success_criteria:
  - url_not_contains: /accounts/login/

#### steps
1. navigate: http://localhost:8015/accounts/login/
2. input: input[name="login"] <- ${TEST_USER_NAME}
3. input: input[name="password"] <- ${TEST_USER_PASSWORD}
4. click: button[type="submit"]
5. assert: url does not contain /accounts/login/

### recipe_id: login.admin
- role: admin（スーパーユーザー）
- version: 1
- login_url: http://localhost:8015/admin/login/
- required_env:
  - 管理者アカウント: nori / teranori@gmail.com
- success_criteria:
  - url_contains: /admin/

#### steps
1. navigate: http://localhost:8015/admin/login/
2. input: input[name="username"] <- nori（または email）
3. input: input[name="password"] <- （別途確認）
4. click: input[type="submit"]
5. assert: url contains /admin/

## 操作レシピ（ログイン以外）

### recipe_id: operation.vket.open-list
- version: 1
- required_env: none
- success_criteria:
  - url_contains: /vket/
  - text: Vketコラボ

#### steps
1. navigate: http://localhost:8015/vket/
2. wait_for_any:
   - text: Vketコラボ
   - url_contains: /vket/

### recipe_id: operation.vket.apply
- version: 1
- required_env:
  - ログイン済み（login.default）
  - Vketコラボの pk 確認済み
- success_criteria:
  - url_contains: /vket/{pk}/apply/

#### steps
1. navigate: http://localhost:8015/vket/{pk}/apply/
2. フォームに日程・LT情報を入力
3. submit

## 主要なURL

| URL | 説明 |
|-----|------|
| `/vket/` | Vketコラボ一覧 |
| `/vket/<pk>/` | コラボ詳細 |
| `/vket/<pk>/apply/` | 参加申請（要ログイン） |
| `/vket/<pk>/status/` | 参加状況確認（要ログイン） |
| `/vket/<pk>/notices/` | お知らせ一覧（要ログイン） |
| `/vket/<pk>/manage/` | 管理画面（要admin） |
| `/vket/<pk>/manage/notices/` | お知らせ管理（要admin） |
| `/vket/<pk>/manage/publish/` | 公開同期（要admin） |
| `/vket/ack/<token>/` | ACK確認（ログイン不要） |
| `/admin/` | Django管理画面 |

## よくある問題と解決策

| 問題 | 原因 | 解決策 |
|------|------|--------|
| ログイン後に403 | CSRFエラー | ページをリロードして再試行 |
| Vketコラボが表示されない | phase=draftのため非公開 | 管理画面でphaseを変更 |

## 更新履歴

- 2026-03-02: 初版作成（Vket再設計実装後のQA用）
