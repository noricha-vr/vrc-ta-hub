# 次にやるべきこと

生成日時: 2026-03-24 23:03 JST  
スキャンスコープ: default

## プロジェクト状況サマリー

| 項目 | 状態 |
|------|------|
| プロジェクト名 | VRC 技術学術ハブ |
| プロジェクトゴール | 既存運用中のイベントHubを維持しつつ、Vketコラボ受付・運営機能を拡張 |
| 実装進捗 | 80%前後（主要機能は稼働、Vket運用導線と一部API/品質整備が残り） |
| テストカバレッジ | 60%前後の体感（テスト資産は多いが CI 対象が限定的） |
| 主要な懸念 | Vket関連のUX改善が継続発生、ドキュメントの陳腐化、CIの監視不足、副作用APIの保護不足 |

## ギャップ分析

### ゴール達成に必須だが未整備
- Vketコラボの現行仕様がドキュメントへ反映されておらず、実装理解と改修判断を誤りやすい
- Vket と event の変更を CI が十分に監視しておらず、回帰を自動で拾えない
- 管理APIに露出している `generate_from_pdf` が no-op で、機能があるように見えて実態がない

### 品質リスク
- 公開中のイベント詳細画面にコンソールエラーの既知不具合が残っている
- Vket/app/event/community の view 層が肥大化しており、変更コストが高い
- README / `.env.example` / 実装 / notes の整合性が崩れている

### 技術的負債
- 副作用ありエンドポイントが `GET + Request-Token` ベース
- Community 詳細と Recurrence API に N+1 っぽいクエリが残る
- `assert` に依存した設定検証や、意図が曖昧な `pass` / コメントアウトコードが残る

## 推奨アクション（優先度順）

### P0: 緊急
- なし

### P1: 高
- [ ] Vketの現行仕様を docs に再定義する
  - 理由: 旧仕様書が現行モデルとズレていて、ここ数日のUI改善判断もコード読解頼みになっている
  - ファイル: `docs/tmp/vket-collab-form-spec.md`, `app/vket/models.py`, `app/vket/views.py`
  - 見積もり: 0.5日

- [ ] CI とローカル回帰導線に `vket.tests` を追加する
  - 理由: Vket改修が続いているのに `.github/workflows/ci.yml` も `scripts/run_tests.sh` も Vket を監視できていない
  - ファイル: `.github/workflows/ci.yml`, `scripts/run_tests.sh`
  - 見積もり: 0.5日

- [ ] `generate_from_pdf` を実装するかAPIから外す
  - 理由: `app/api_v1/serializers.py` でフラグを受け取るのに実処理がなく、API利用者に誤解を与える
  - ファイル: `app/api_v1/serializers.py`, 関連する event 処理
  - 見積もり: 0.5-1日

- [ ] README / `.env.example` / settings の環境変数仕様を揃える
  - 理由: `OPENAI_API_KEY`, `GOOGLE_CALENDAR_ID`, `REQUEST_TOKEN` の必須性が実装と文書でズレている
  - ファイル: `README.md`, `.env.example`, `app/website/settings.py`
  - 見積もり: 0.5日

- [ ] 副作用ありバッチ入口を `GET` から `POST` に寄せる設計方針を決める
  - 理由: 同期・生成系が `GET + Request-Token` で動いていて、運用事故時の影響が大きい
  - ファイル: `app/event/views.py`, `app/event/views_llm_generate.py`
  - 見積もり: 1日

- [ ] 公開中の `event/detail/{id}` コンソールエラーを先に潰す
  - 理由: 既知不具合として backlog に残っていて、ユーザー向け画面の信頼性に直結する
  - ファイル: `docs/notes/todo.md`, 該当テンプレート/JS
  - 見積もり: 0.5-1日

### P2: 中
- [ ] Vket backlog をまとめて実装する
  - 理由: 「参加日ラベルに時間帯併記」「LT時刻の柔軟化」「最近追加したUX改善Issue」が同じ文脈で溜まっている
  - ファイル: `app/vket/forms.py`, `app/vket/templates/vket/*.html`, `docs/notes/todo.md`
  - 見積もり: 1-2日

- [ ] `app/vket/views.py` をユースケース単位で分割する
  - 理由: 1,100行超で、権限・進捗更新・通知・公開同期が混在している
  - ファイル: `app/vket/views.py`
  - 見積もり: 2-3日

- [ ] Community詳細と Recurrence API のクエリ効率を改善する
  - 理由: `prefetch_related` の使い方と `count()` の繰り返しで N+1 が起きやすい
  - ファイル: `app/community/views.py`, `app/api_v1/serializers.py`
  - 見積もり: 0.5-1日

- [ ] `docs/notes/status.md` の役割を整理し、現状に追従させる
  - 理由: 進捗ノートが古く、`/note use` や今後の `/next` の精度を落としている
  - ファイル: `docs/notes/status.md`
  - 見積もり: 0.5日

- [ ] 設定検証と意図不明コードの軽い清掃をする
  - 理由: `assert` 依存の env 検証、空フック、コメントアウト残骸が読みづらさを増している
  - ファイル: `app/website/settings.py`, `app/event/views.py`, `app/api_v1/recurrence_preview.py`, `app/user_account/views.py`
  - 見積もり: 0.5-1日

## 並列実行可能グループ

### Group 1（依存関係なし、すぐ着手可）
- README / `.env.example` / settings の整合修正
- CI / `scripts/run_tests.sh` への `vket.tests` 追加
- `docs/notes/status.md` の更新

### Group 2（Group 1 と並列でも可だが、仕様整理後のほうが安全）
- Vket現行仕様の文書化
- Vket backlog の UX 改修

### Group 3（Group 2 の理解があると着手しやすい）
- `generate_from_pdf` の方針決定と実装/削除
- `GET + Request-Token` エンドポイントの再設計

### Group 4（後追いの品質改善）
- Vket / event / community の view 分割
- N+1 改善
- 意図不明コードの清掃

## 分析詳細

### Goal & Architecture
- Vket の実装は再設計済みだが、仕様書が旧モデル前提のまま残っている
- README と notes が現行運用に追従しておらず、実装より前に認知負荷が高い
- Lite DDD 方針に対して、主要ロジックは view 層へ集中している

### Implementation Status
- `generate_from_pdf` は API に出ているが未実装
- `event/detail/{id}` のコンソールエラーが既知不具合として未消化
- Vket 参加日ラベル改善など backlog は明確だが未着手

### Quality & Test
- テストファイル自体は多いが、CI の実行範囲が狭い
- `scripts/run_tests.sh` でも `vket.tests` が漏れている
- `app/event/views.py`, `app/community/views.py`, `app/vket/views.py` が巨大化

### Security & Performance
- 副作用エンドポイントが `GET + Request-Token` で保護されているだけ
- Community 詳細と Recurrence 集計に N+1 懸念
- 必須環境変数チェックが `assert` 依存で、設定検証として弱い
