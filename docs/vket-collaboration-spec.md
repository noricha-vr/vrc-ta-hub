# Vketコラボ現行仕様

`app/vket` の現行実装を前提に、主催者画面・運営画面・公開反映の流れをまとめた資料です。

## モデル構成

### `VketCollaboration`
- コラボ全体の開催情報を持つ
- 主な項目:
  - `name`, `slug`
  - `period_start`, `period_end`
  - `registration_deadline`, `lt_deadline`
  - `phase`
  - `settings_json`

### `VketParticipation`
- 1コラボ × 1集会 の参加情報
- 主な項目:
  - `community`
  - `requested_date`, `requested_start_time`, `requested_duration`
  - `confirmed_date`, `confirmed_start_time`, `confirmed_duration`
  - `progress`
  - `stage_registered_at`
  - `published_event`
  - `organizer_note`, `admin_note`

### `VketPresentation`
- 参加集会に紐づく LT 情報
- 複数件登録できる
- 主な項目:
  - `speaker`, `theme`
  - `requested_start_time`, `confirmed_start_time`
  - `status`
  - `published_event_detail`

### `VketNotice` / `VketNoticeReceipt`
- 運営から参加集会への通知
- `requires_ack=True` の通知は受信側で確認状態を持つ

## フェーズと進捗

### `phase`
`VketCollaboration.phase` はコラボ全体の運用フェーズ。

- `draft`: 下書き
- `entry_open`: 参加受付中
- `scheduling`: 日程調整中
- `lt_collection`: LT情報回収中
- `announcement`: 告知・公開準備
- `locked`: 公開同期・確定後
- `archived`: 終了済み

### `progress`
`VketParticipation.progress` は各集会の進捗。

- `not_applied`: 未参加
- `applied`: 参加申込済み
- `stage_registered`: Vket stage 登録済み
- `rehearsal`: リハーサル段階
- `done`: 公開反映完了

`phase` は全体運用、`progress` は各集会の進み具合で、意味が別です。

## 主催者画面の流れ

1. `ApplyView`
   - 主催者が参加希望日・希望時刻・開催時間・備考を登録
   - LT は formset で複数登録できる
2. `ParticipationStatusView`
   - 進捗ステップ、確定日程、LT情報、最新通知を確認
   - `progress=applied` の間は Vket stage 登録案内と `登録完了` ボタンを表示
3. `NoticeListView`
   - 自集会あてのお知らせ一覧と ACK 状態を確認

## 運営画面の流れ

1. `ManageView`
   - 参加集会の一覧、日程マトリクス、進捗の全体把握
2. `ManageParticipationUpdateView`
   - 確定日程、備考、進捗などを更新
3. `ManageNoticeListView` / `ManageNoticeCreateView` / `ManageNoticeUpdateView`
   - お知らせ作成・編集・配信状況確認
4. `ManagePublishView`
   - 確定済み LT から `Event` / `EventDetail` を公開反映

## 権限と締切

### 主催者側
- `ApplyView` は基本的に集会 owner が操作
- `registration_deadline` までは日程編集可能
- `lt_deadline` までは LT 情報と備考を編集可能

### 運営側
- `app/vket/views.py` の `_is_vket_admin()` を満たすユーザーが運営画面へ入れる
- 現行実装では `is_superuser` または `is_staff`

## 画面仕様メモ

### 参加申請画面
- 参加希望日は、その集会の `Event.date` からコラボ期間内の候補だけを出す
- LT欄は 1 件以上入力できるが、空行は保存しない
- 備考欄は主催者から運営への連絡用途

### 参加状況画面
- 最新お知らせは直近 2 件を表示
- `requires_ack` 未確認件数があると上部に警告バナーを出す
- Vket stage 登録は外部サイト作業後に Hub 側で `登録完了` を押して進める

### 公開反映
- `ManagePublishView` 実行時、未公開 LT から `EventDetail` を生成する
- 反映後は `published_event` / `published_event_detail` が紐づき、進捗は `done` へ進む

### 公開同期の運用

1. ACTIVE な参加の確定日程・確定開始時刻・確定開催時間をすべて入力する。いずれかが未設定の参加は公開同期でスキップされ、確定済みの発表だけが `EventDetail` へ同期される。
2. コラボのフェーズを `locked` にしてから、運営管理画面の「公開同期（Event作成）」を実行する。
3. 公開同期は `Event` / `EventDetail` を作成または更新し、`VketParticipation.published_event` をコラボ本体イベントとして記録する。日時ロック判定はこの関連を優先し、未設定時は確定日時一致をフォールバックにする。
4. 完了メッセージの件数と各参加の進捗が「完了」であることを確認する。日時差分の警告がないことは、日付と開始時刻の同期確認に使う。
5. 公開同期は再実行できる。確定日程・確定開始時刻・確定開催時間を運営画面で変更した場合は、必ず再実行して公開イベントへ反映する。
6. 公開イベントを直接変更した可能性がある日時差分の警告が出た場合、自動修正はしない。意図する日時を確認し、確定日程を合わせるか、公開同期を再実行して公開イベントを確定日程へ戻す。`published_event` が未設定の参加は警告で比較する公開イベントがないため対象外であり、先に公開同期を実行する。
