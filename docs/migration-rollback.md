# マイグレーション Rollback 手順

Django migration を本番・staging・開発環境で巻き戻すための手順をまとめる。障害発生時に**事前手順を読み返さずに最短で対応**できるよう、コマンド・実例・落とし穴を一通り集約してある。

## なぜ Rollback 手順が必要か

- 本番反映直後にバグや性能劣化が発覚した場合、**先にスキーマを戻す**ことで影響範囲を最小化できる
- migration の中には rollback できないもの（DROP TABLE / `noop` reverse）があり、**事前に判別**しておかないと「戻せると思っていたが戻せない」という最悪パターンに陥る
- AWS RDS（本番）/ MySQL（ローカル）の双方で同じ判断基準を共有しておくと、障害対応時の意思決定が速くなる

## 基本コマンド

### 1. 現在の適用状態を確認する

```bash
docker compose exec vrc-ta-hub python manage.py showmigrations <app>
```

`[X]` が付いているものが適用済み。直前に流したものが何かを必ず確認してから rollback に進む。

```bash
# 全アプリを一気に確認
docker compose exec vrc-ta-hub python manage.py showmigrations
```

### 2. これから実行される SQL を確認する

```bash
docker compose exec vrc-ta-hub python manage.py migrate <app> <revision_name> --plan
```

`--plan` を付けると、実際の DB 変更はせず「これから何が走るか」だけを表示できる。本番でいきなり `migrate` を叩く前に必ず通す。

```bash
# 特定の revision に向かう SQL を確認
docker compose exec vrc-ta-hub python manage.py sqlmigrate <app> <revision_name>
```

### 3. 特定リビジョンへ rollback する

```bash
docker compose exec vrc-ta-hub python manage.py migrate <app> <target_revision>
```

`<target_revision>` には**戻したい先のリビジョン名**を指定する。`0021` を取り消して `0020` の状態に戻したいなら `migrate community 0020`。

```bash
# アプリ全体を初期状態に戻す（開発環境のリセット用、本番では絶対に使わない）
docker compose exec vrc-ta-hub python manage.py migrate <app> zero
```

## 実例 1: 直近 migration を 1 つ戻す

`community/migrations/0021_xxx.py` を適用した直後に問題が見つかったケース。

```bash
# 1) 現状確認（0021 が [X] になっているはず）
docker compose exec vrc-ta-hub python manage.py showmigrations community

# 2) 0020 まで戻す SQL を確認
docker compose exec vrc-ta-hub python manage.py migrate community 0020 --plan

# 3) 実行
docker compose exec vrc-ta-hub python manage.py migrate community 0020

# 4) 再度確認（0021 の [X] が外れる）
docker compose exec vrc-ta-hub python manage.py showmigrations community
```

`AlterField` 系（`app/community/migrations/0020_alter_community_notification_webhook_url.py` のようにバリデータを変えるだけのもの）は副作用が小さく、rollback も安全に通る。

## 実例 2: data migration を含む場合の注意

`RunPython` を含む migration は、`reverse_code` に何を渡したかで挙動が変わる。

```python
# 例: app/user_account/migrations/0009_hash_apikeys.py 相当
operations = [
    migrations.RunPython(hash_existing_api_keys, migrations.RunPython.noop),
]
```

- `migrations.RunPython.noop` が `reverse_code` に渡されているケースは「**スキーマ上は戻せるが、データは戻らない**」。上記の例では、ハッシュ化された API キーは平文には**戻らない**ため、rollback してもユーザーは再発行が必要
- データを安全に戻したい場合は、新しい migration 側で `reverse_code` に**実際の戻し処理**を書いておく必要がある（DB バックアップから復元する運用と組み合わせる）

判断の流れ:

1. 対象 migration の `reverse_code` を読む
2. `noop` なら「rollback してもデータは元に戻らない」前提でユーザー影響を確認
3. 影響が大きいなら、rollback せず**新しい forward migration で修正**する方を選ぶ

## 実例 3: 本番 (Cloud Run) での rollback

vrc-ta-hub の本番は Cloud Run + Cloud SQL（AWS RDS の MySQL は別系統）。rollback の選択肢は 2 通り。

### 方式 A: Cloud SQL Proxy 経由でローカルから実行

```bash
# 1) Cloud SQL Proxy を起動（別ターミナル）
cloud-sql-proxy <PROJECT>:asia-northeast1:<INSTANCE>

# 2) ローカルの .env.production.local を読み込んだ状態で migrate
docker compose --env-file .env.production.local exec vrc-ta-hub \
  python manage.py migrate community 0020 --plan

# 3) プランを確認して問題なければ本番に適用
docker compose --env-file .env.production.local exec vrc-ta-hub \
  python manage.py migrate community 0020
```

- `.env.production.local` は Git 管理外。本番 DB の認証情報を持っている人だけが実行できる
- 必ず `--plan` を 1 回挟む。本番でいきなり実行しない

### 方式 B: gcloud run jobs で実行

```bash
# Cloud Run Job として migrate を実行
gcloud run jobs execute vrc-ta-hub-migrate \
  --region=asia-northeast1 \
  --args="migrate,community,0020"
```

- CI/CD パイプラインに組み込みやすいが、ジョブ実行ログから rollback の成否を確認する必要がある
- 緊急時は方式 A の方が手数が少なく早い

## 危険な migration の見分け方

PR レビュー時点で**「rollback コストが高い」**と判断するためのチェックリスト。

| 種類 | 何が危険か | 検知方法 |
|------|-----------|---------|
| NOT NULL カラム追加（デフォルト値なし） | rollback で `ALTER TABLE` が走るが、forward 適用時に既存行のデフォルト値を埋める必要があり、適用そのものが失敗しやすい | `AddField(..., null=False)` で `default` が無いか確認 |
| カラム rename / drop | rollback でデータ復元不可。アプリ側コードと migration のタイミングがズレるとダウンタイム発生 | `RenameField` / `RemoveField` |
| 大量データ更新（RunPython で全行更新） | rollback 時に再度全行スキャン。本番では数十分単位で DB ロックがかかる | `RunPython` + `.objects.all()` パターン |
| インデックス作成・削除 | MySQL では `ALGORITHM=COPY` でテーブルロックがかかるケースがある | `AddIndex` / `RemoveIndex` |
| 外部キー追加 | 既存データに参照整合性違反があると失敗。rollback も同様 | `AddField` with `ForeignKey` |

該当する migration は、**dev / staging で必ず dry-run** してから本番に出す。

## Rollback 不可能なケース

以下のパターンは「rollback コマンドは通るが、実質的に戻らない」または「そもそも実行不能」になる。

### 1. data migration で reverse_code が `noop`

`migrations.RunPython.noop` を `reverse_code` に渡している migration は、**戻すコマンドは成功するがデータは戻らない**。
具体例: 平文→ハッシュ化のような不可逆変換、ID の振り直し、論理削除フラグの一括更新。

### 2. DROP TABLE / DROP COLUMN を含む migration

`DeleteModel` / `RemoveField` を含む migration は、rollback でテーブル・カラムが再作成されるが、**中身のデータは戻らない**。
DB バックアップから別途復元する必要がある。

### 3. 複数アプリにまたがる依存

A アプリの 0010 が B アプリの 0005 に依存している場合、A だけ戻そうとすると B 側も巻き込まれる。`--plan` で必ず連鎖を確認する。

## ベストプラクティス

- **dry-run を staging で必ず通す**: 大型 migration（NOT NULL 追加 / カラム rename / 大量 RunPython）は staging で forward + rollback の両方を試す
- **本番反映前に DB バックアップ**: Cloud SQL の自動バックアップに加え、`mysqldump` でスナップショットを取ってから流す
- **migration は小さく分ける**: 1 つの migration に複数の責務を詰めない（スキーマ変更とデータ移行は分離）
- **`reverse_code` を書く**: data migration には、可能な限り戻せる reverse_code を書く。書けない場合はその旨をコメントに残す
- **本番反映直後は 10 分監視**: Cloud Run のリクエストエラー率・p95 レイテンシを確認し、異常があれば即 rollback できる体制を保つ
- **コードと migration の前方互換**: アプリコードは「migration 適用前」「適用後」のどちらでも動く状態にしておく。ロールバック時の安全弁になる

## 参考

- 良い `reverse_code` の例: [`app/user_account/migrations/0009_hash_apikeys.py`](../app/user_account/migrations/0009_hash_apikeys.py)（不可逆処理のため `noop` を明示）
- シンプルな `AlterField` の例: [`app/community/migrations/0020_alter_community_notification_webhook_url.py`](../app/community/migrations/0020_alter_community_notification_webhook_url.py)
- Django 公式: <https://docs.djangoproject.com/en/5.2/topics/migrations/>
