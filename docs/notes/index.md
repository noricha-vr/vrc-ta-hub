# Notes Index

## how/

| ファイル | 概要 |
|----------|------|
| cloud-run.md | Cloud Run 運用パターン |
| db-sync.md | 本番DB↔ローカルDB同期 手順 |
| drf-spectacular.md | DRF Spectacular パターン |
| gemma-4.md | Gemma 4 運用パターン |
| django-pagination.md | Djangoページネーション入力検証パターン |
| discord-auth.md | Discord OAuth 競合解消パターン |
| github-pages-json.md | VRChatワールド向けJSON配信（toGithubPagesJson） |
| vket-collab.md | Vketコラボ運営パターン |

## logs/

### 2026-04/

| ファイル | 日付 | 概要 |
|----------|------|------|
| gemma-4-01.md | 04-08 | Gemma 4 の常駐モデル候補を整理した |
| vket-collab-01.md | 04-07 | 公開イベント一覧の Google カレンダー URL 生成をスキーマ不整合に強化 |
| db-sync-01.md | 04-06 | 本番DBからローカルへの初回同期 |
| cloud-run-02.md | 04-06 | Cloud Run preview host を nginx 前段でも正規化 |
| cloud-run-03.md | 04-07 | Cloud Run preview host 判定を明示 service 名に寄せた |
| cloud-run-01.md | 04-05 | Cloud Run の古い tagged revision URL cleanup |
| drf-spectacular-01.md | 04-01 | GatheringListSerializer の schema 生成エラー修正 |

### 2026-03/

| ファイル | 日付 | 概要 |
|----------|------|------|
| cloud-run-01.md | 03-27 | Cloud Run 本番切替・JSON再生成・リビジョンURL DisallowedHost |
| django-pagination-01.md | 03-29 | 集会一覧の壊れた page クエリで 500 エラー |
| discord-auth-01.md | 03-24 | Discord再連携で競合アカウントを自己回復できるようにした |
| vket-collab-01.md | 03-21 | CommunityMemberロール不正値で Vket ApplyView 403 |
| vket-collab-02.md | 03-25 | Vketコラボ機能拡充（権限・Progress・FormSet・バナー） |
| vket-collab-03.md | 03-31 | Vket期間中のEventDetail日時変更をロック |
| discord-auth-02.md | 03-24 | Discord本体アカウントへ staff 所属だけを追加して復旧した |
| discord-auth-03.md | 03-24 | 元の owner アカウントから Discord を切り離して個人アカウントへ移した |
| github-pages-json-01.md | 03-27 | テストデータがVRChatワールドカレンダーに混入 |
| github-pages-json-02.md | 03-27 | 最終アセットのジャンル互換を確認 |
| security-audit-01.md | 03-27 | セキュリティ監査対応: XSS修正・セキュリティヘッダー・print文マスク・エラーレスポンス |

### 2026-02/

| ファイル | 日付 | 概要 |
|----------|------|------|
| vket-collab-01.md | 02-10 | Vketコラボ受付フォーム仕様策定 |
