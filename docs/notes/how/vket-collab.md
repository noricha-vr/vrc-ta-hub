# Vketコラボ運営パターン

## 管理に使う2つのビュー

過去の運営ではGoogleスプレッドシートで2つのシートを使い分けていた。
Hub上でこの2ビューをDB駆動で再現することが目標。

### 1. 登壇者テーブル（進捗管理）

| カラム | 内容 |
|--------|------|
| 集会名 | Community.name |
| 日付 | Event.date |
| 時刻 | Event.start_time（集会ごとに異なる: 21:00, 21:30, 22:00等） |
| 登壇者 | EventDetail.speaker |
| テーマ | EventDetail.theme |
| タスク | 要確認等のステータスメモ |

### 2. 日程表マトリクス（バッティング可視化）

- 行: 日付 × 集会名
- 列: 時間スロット（21:00, 21:30, 22:00, 22:30）
- セル: ドット（●）で開催時間帯を可視化
- 追加列: 撮影スタッフ、備考

同日に複数集会が入ることは普通にある（例: 12/10 ML集会 + Web技術集会）。
時間帯が重なっていても基本OK（バッティングは警告のみ）。

## 運営ワークフロー

### 調整の順序（重要）

```
1. 参加表明 + 日程登録（締切1）
   ↓
2. 管理者がスケジュール調整（日程・時間の変更）
   ↓  ← ここまでに登壇者が決まっている必要はない
3. 登壇者・テーマの登録（締切2）
   ↓  決まっていなければ各集会に催促
4. スケジュール確定・公開
```

- 日程・時間の調整は登壇者が決まる**前に**完了する
- スーパーユーザーは参加者の日程・時間を直接変更できる必要がある
- 希望日は基本通る（バッティング調整が主目的ではない）

## 教訓

- 問題: Googleフォーム + スプレッドシートだとHubのデータと紐づかない
- 解決: Hub上で参加登録→スケジュール管理→LT登録を一気通貫で行う
- 教訓: 管理者が一番必要なのは「全集会の進捗が一覧できるテーブル」

## CommunityMember ロール設定

### ロール値は TextChoices を必ず確認してから設定する
- 問題: `CommunityMember` のロールを `organizer` に設定したが、実際の `Role.OWNER` は `'owner'`。不正値が保存されても Django はエラーを出さず、権限チェックで静かに失敗する
- 解決: `CommunityMember.Role.choices` → `[('owner', '主催者'), ('staff', 'スタッフ')]` を確認し、Enum定数を使って設定する（`m.role = CommunityMember.Role.OWNER`）
- 教訓: TextChoices の値は直感と異なることがある（主催者 = owner ≠ organizer）。文字列リテラルではなく必ず Enum 定数を使う

### Vket 管理者権限（参照: PR #115）
- `_is_vket_admin(user)`: `is_superuser` または `is_staff` で判定（`vket/views.py`）
- コラボ一覧/詳細の下書き表示、ApplyView の全権限付与に使用
- ApplyView 自体は `membership.role == CommunityMember.Role.OWNER` もチェック
- テスト用に集会主催者権限を付与する場合は `OWNER` ロールが必要

### Progress 状態の運営フロー（参照: PR #115）
```
NOT_APPLIED → APPLIED → STAGE_REGISTERED → LT_REGISTERED
→ REHEARSAL → EVENT_WEEK → LT_MATERIAL_UPLOADED → AFTER_PARTY → DONE
```
- 旧: `SCHEDULE_CONFIRMED / LT_PENDING / LT_SUBMITTED` の3段階
- 新: 実際の運営タスクに対応した8段階（各段階で管理者が進捗を更新）

## EventDetail 日時ロック（参照: PR #140）
- 問題: Vket 開催期間中に集会側が `EventDetail.start_time` / `duration` を変えると、運営が調整した日程が崩れる
- 解決: `VketParticipation.lifecycle=active` かつ `collaboration.period_start <= Event.date <= period_end` の期間は、superuser 以外の日時変更を Web/UI と API の両方でブロックする
- 教訓: 運営が確定したスケジュールは、表示の readonly だけで済ませず、フォーム/API まで共通ルールで守る

## 公開導線のスキーマ耐性（参照: PR #221）
- 問題: 公開イベント一覧のような read path で `event.details.filter(...).exists()` のような余計な ORM 評価をすると、Vket 側の列追加直後に古い DB スキーマを踏んだ環境で 500 に巻き込まれうる
- 解決: prefetch 済みデータを優先利用し、fallback が必要でも `.values_list()` で必要最小列だけを 1 クエリで読む
- 教訓: 公開ページは「正しいこと」だけでなく「スキーマ不整合に巻き込まれにくいこと」も重視して、不要な追加クエリを避ける
