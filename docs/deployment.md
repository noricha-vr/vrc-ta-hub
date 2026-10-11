# デプロイ

VRC技術学術ハブのデプロイ運用メモ。Cloud Run / Cloud Build を前提とする。

## Cloud Build Trigger のbranch filter

`cloudbuild.yaml` にbranch filterは定義できない。実行対象は
Google Cloud側のBuild Trigger設定で限定する。GitHub Actionsの`safe-to-test` labelは
テスト実行の承認だけで、build / deployの承認には使わない。

- production Triggerのbranch filterはexact `^main$` とする
- 開発用のデプロイ先（dev Service / dev Trigger）は廃止した。公開前の確認は本番サービスの
  `canary` タグURLで行う（タグはデプロイ後のカナリア検証時に付与する）。wildcard Triggerは作らない
- `fix-flow/isolation-task-*`、PR head、その他のfeature branchをCloud Build Triggerにmatchさせない
- Trigger作成・変更後はGoogle Cloud側のbranch regexを読み戻し、isolation branchの
  pushでbuildが作られないことを確認する

GitHub Actionsの隔離PRゲートは[テスト方針](testing.md#github-actions-の隔離pr承認ゲート)を参照。
必要なTrigger filterを確認できない環境では、自動deployを有効化しない。

## migration適用用のCloud Run Job

Cloud BuildはDjango migrationを自動実行しない（判断記録:
[issue-464](research/issue-464-cloud-run-job-migration.md)）。本番migrationは
Cloud Run Job `vrc-ta-hub-migrate` を人間の判断で実行して適用する。

Jobが存在しない場合は先に作成する。稼働中のCloud Runサービスからイメージ・
環境変数・シークレット・サービスアカウントを引き継ぐ冪等スクリプトを使う。

```bash
./scripts/create_migrate_job.sh
```

適用（全アプリ）と適用状況の確認:

```bash
# 全アプリのmigrateを適用（Jobのデフォルト引数）
gcloud run jobs execute vrc-ta-hub-migrate \
  --region=asia-northeast1 --project=vrc-ta-hub --wait

# 個別のmigrationだけ当てる場合はJob定義の引数を差し替えてから実行し、必ず戻す。
# execute --args による実行時上書きは、この環境ではAPIがoverridesを受け付けず失敗する
# （Unknown name "priorityTier"）。区切りは ^|^ を使う（既定のカンマ区切りだと壊れる）。
gcloud run jobs update vrc-ta-hub-migrate \
  --region=asia-northeast1 --project=vrc-ta-hub \
  --args='^|^manage.py|migrate|user_account|0016|--noinput'
gcloud run jobs execute vrc-ta-hub-migrate \
  --region=asia-northeast1 --project=vrc-ta-hub --wait
gcloud run jobs update vrc-ta-hub-migrate \
  --region=asia-northeast1 --project=vrc-ta-hub \
  --args='^|^manage.py|migrate|--noinput'

# 未適用の一覧（read-only）。引数の差し替え・復元・ログ判定まで面倒を見る
./scripts/check_pending_migrations.sh
```

任意の管理コマンドも同じ Job で実行できます。Job の引数を一時的に差し替え、
`execute --async` で実行名を取って完了まで待ち、その実行の標準出力・標準エラーのログを表示し、
終了時に元の引数へ戻します。実行が失敗した時も、ログを表示してからエラーで終わります。
ログは同じ内容が 2 回続けて取れるまで読み直します（Cloud Logging の取り込み遅延）。
`EXPECT_LOG_PREFIX` を指定すると、その文字列で始まる行が現れるまで成功にしません。
ログを取得できない場合や復元に失敗した場合はエラーになります。

```bash
# 実行前に KEEP_COMMUNITY_ID_A / KEEP_COMMUNITY_ID_B に残す集会の ID を設定する
EXPECT_LOG_PREFIX=RECORDING_OPT_IN_DONE ./scripts/run_manage_command.sh apply_recording_opt_in \
  --keep-community-id "$KEEP_COMMUNITY_ID_A" --keep-community-id "$KEEP_COMMUNITY_ID_B" --dry-run
```

`PROJECT_ID` / `REGION` / `JOB_NAME` で対象を上書きできます。gcloud の認証設定は
呼び出し側の `CLOUDSDK_CONFIG` / `CLOUDSDK_ACTIVE_CONFIG_NAME` を引き継ぎます。
引数に `|` は使えません。同じ Job の引数を変更する処理は同時に実行しないでください。Job の引数が既定（`manage.py migrate --noinput`。`IDLE_ARGS` で変更可）でない時は、ほかの実行の最中とみなして何も変えずに断ります。差し替えた後に別の実行が引数を変え、意図と違うコマンドが動いた時は、ログを出した上で失敗にします（その実行が何を変えたかを必ず確かめてください）。
撮影の変更前に `community.0032_alter_community_recording_allowed_default` を適用し、
dry-run の対象件数・URL があり変更しない発表・残す集会の報告を確認してください。
移行コマンドは、新しいリビジョンにトラフィックを 100% 切り替えた後に実行します（切替前は旧リビジョンが `recording_allowed=True` で集会を作れるため）。冪等なので、切替後にもう一度流しても差分だけを当てます。
`--keep-community-id` は必須で、撮影許可を残す集会の ID ごとに繰り返して指定します。
名前による指定はできません。指定した ID が一つでも存在しなければ、変更前にエラーになります。
残す集会の現在の撮影許可はそのまま維持し、それ以外の集会の撮影許可をオフにします。

発表は論理削除済みも含め、次の順で最初に該当する規則だけを適用します。

- a: 残す集会以外の、今日以降・YouTube URL が NULL または空文字の発表は、既に「禁止」でなければ「禁止」にします。
- b: 過去の発表で既定値のまま「公開」になっていたものは、集会や URL の有無を問わず「許可（公開しない）」にします。
- c: 残す集会以外の、過去・URL なしの発表で、b に該当せず既に「禁止」でなければ「禁止」にします。
- d: それ以外は変更しません。

「過去」は Django のローカル日付で今日より前です。既定値のままの公開は、
`recording_policy=public`、`created_at < --defaults-before`、追加情報に「動画撮影」を含まない
（NULL も含まない扱い）のすべてを満たすものです。
`--defaults-before` の既定値は `2026-09-28T16:57:12+00:00` です。

dry-run でも、各規則の件数と旧値の内訳、規則 b の集会・URL 別の内訳、
規則 a の発表の ID・開催日・旧値・集会 ID を出力します。
残す集会以外の URL があり変更しない発表（「禁止」を除く）の一覧と、
今日以降で URL がある発表の件数・ID も報告します。
残す集会ごとに ID・名前・現在の撮影許可、変更前の既定値 public の件数、
変更後に public のまま残る予定件数、今日以降の発表一覧、発表の有無によらない次の開催日を確認できます。

実際のデータ変更は承認後に `--dry-run` を外して実行し、
`RECORDING_OPT_IN_BACKUP` 行を実行時の控えとして保存してください。
実行時は `RECORDING_OPT_IN_APPLIED` 行に実際に変えた ID と、その時の旧値が、集計後に値が変わって飛ばした行があれば `RECORDING_OPT_IN_SKIPPED` 行に出ます。
戻す時は BACKUP ではなく APPLIED の ID と旧値を使ってください（飛ばした行は主催者や登壇者の新しい選択です）。
この行の JSON は、変更対象の集会と発表の ID をキーにした旧値だけを持ちます。
更新はトランザクション内で行い、集計後に旧値や対象条件が変わった行は上書きしません。
再び dry-run を実行し、変更対象件数を確認してください。

デプロイ前チェックの正本は [deploy-check.toml](deploy-check.toml)（deploy-watchが読む）。
`[migrations]` に上記コマンドを定義してあるため、トラフィック切替前に未適用migrationが
無いことを必ず確認する。

`user_account.0015_backfill_verified_email_addresses` は所有権が競合するデータ
（別ユーザーが所有する `EmailAddress` 等）があると監査で停止する。停止した場合は
所有者を推測して修正せず、[migration-rollback.md](migration-rollback.md#user_account-0015-の適用前監査)
の監査コマンドで対象を確認してから再実行する。

### メールアドレスの持ち主の表（user_account 0017 / 0018）の先行適用

`user_account.0017_emailownership` は持ち主の記録 `user_account_emailownership` を作り、
`0018_backfill_email_ownership` は既存のアカウントから記録を埋める。新しいコードは
`CustomUser.save()` と `is_email_in_use` でこの表を読み書きするため、未適用のまま新revisionへ
トラフィックを流すと、登録・メール変更・副アドレスの確認・プロフィール保存が500になる。
0016と同じく、トラフィック切替より前に適用する。

記録のアドレスは前後の空白を除いて小文字にそろえてあり、0017はMySQLでこの列だけを `utf8mb4_bin`（完全一致）にする。
DBの一意判定が、監査・0018・記録の同期と同じ判定（小文字にした値の完全一致）になる。
アクセントだけ違うアドレスは別のアドレスとして記録する。主アドレス同士は、これまでどおり
`CustomUser.email` の一意制約（DBの既定の照合順序）が止める。

切替の窓: 0018を当ててからトラフィックの切替が終わるまで、旧revisionは記録を更新しない。
旧revisionの変更でずれた記録（`missing` / `stale`）は `--repair` で直せる。一方、旧revisionと新revisionの
処理が同じアドレスで交差すると、持ち主が2人になる（`conflicts`）ことがあり、これは `--repair` では直せない。
この危険は保護の無い今の本番と同じで、広がってはいない。窓を短くするため、アクセスの少ない時間帯に行い、
0017 / 0018を当てたら間を空けずに切り替える。

順番は次のとおり。監査コマンド `audit_email_ownership` はアドレスを出さず件数だけを出す。

1. mainへのマージ後、`--no-traffic` の新revisionができるのを待つ
2. `./scripts/create_migrate_job.sh` でJobを新イメージに更新する
3. 適用前の監査（読み取り専用）。Jobの引数を差し替えて実行し、ログで `conflicts=0` を確かめてから引数を戻す

   ```bash
   gcloud run jobs update vrc-ta-hub-migrate \
     --region=asia-northeast1 --project=vrc-ta-hub \
     --args='^|^manage.py|audit_email_ownership'
   gcloud run jobs execute vrc-ta-hub-migrate \
     --region=asia-northeast1 --project=vrc-ta-hub --wait
   gcloud run jobs update vrc-ta-hub-migrate \
     --region=asia-northeast1 --project=vrc-ta-hub \
     --args='^|^manage.py|migrate|--noinput'
   ```

   表が無い段階なので `addresses` と `conflicts` だけが出る。`conflicts` が1以上なら止める。
   持ち主を推測して直さない（0018も同じ条件で止まる）
4. 0017 / 0018を適用し（`gcloud run jobs execute vrc-ta-hub-migrate --region=asia-northeast1 --project=vrc-ta-hub --wait`）、
   `./scripts/check_pending_migrations.sh` で未適用ゼロを確かめる
5. 間を空けずにトラフィックを新revisionへ切り替える
6. 切替後の監査。3と同じ手順で流し、`conflicts=0 missing=0 stale=0` を確かめる。
   監査は書き込みと同時に読むので、流している間の変更で数が一時的にずれることがある。0でなければもう一度流し、残ったものを扱う
   - `missing` / `stale` だけの時は、引数を `--args='^|^manage.py|audit_email_ownership|--repair'` にして合わせ直す（書き込みあり）。
     `--repair` はユーザーごとに、そのユーザーの行をロックしてから読み書きする。新revisionの保存も同じロックを取るので、
     新revisionが動いている間に流してよい。最後に引数を `migrate|--noinput` へ戻す
   - `conflicts` が1以上の時は、持ち主を推測して直さない。対象のアカウントを確かめ、人が解消してから監査をやり直す。
     解消するまで、記録を持てない側のユーザーは、プロフィールの保存などユーザー全体の保存が失敗する

戻し方と、0018が止まった時の扱いは
[migration-rollback.md](migration-rollback.md#user_account-0017--0018-の適用前監査と戻し方) を参照。

### DatabaseCache migrationの先行適用

Cloud Runではログイン失敗回数とDRF throttleを複数インスタンス間で共有するため、
default cacheが `login_rate_limit_cache` テーブルを使う。新revisionにトラフィックを
入れる前に `user_account.0016_login_rate_limit_cache` を必ず適用する。未適用のまま
トラフィックを流すと、cacheテーブル不在でトップページが500になる。

実行ログで成功を確認し、`showmigrations user_account`で `0016` が適用済みに
なってからdeploy・トラフィック切り替えへ進む。新revisionが動作中にこの
migrationを戻すとログインとAPI throttleがDBエラーになるため、rollback時は
先に旧revisionへトラフィックを戻す。

Cloud Run以外はLocMemCache（`REDIS_URL` 設定時は既存Redis）を使う。Cloud Runでは
DRF throttleもDatabaseCacheに乗るため、複数インスタンス間の精度が上がる一方、
Cloud SQLのread/writeとレイテンシを監視する。`cache.clear()` を行う管理コマンドは
ログイン失敗回数とDRF throttleも一括解除するため、必要時のみ実行する。

DatabaseCacheはランダムemailによるキー大量生成で既定300件から有効な制限キーが
押し出されないよう、`MAX_ENTRIES=100000`、`CULL_FREQUENCY=4`とする。パスワード
リセット完了時はallauthが対象emailの失敗カウンタを解除し、正規ユーザーの回復手段になる。

### 期限切れcache行の定期削除

`expires`にはindexがある。Cloud SQLの不要行とcull負荷を抑えるため、次の処理を
1時間ごとを目安に、トラフィックの少ない時間帯で実行する。削除件数だけを出力し、
cache keyやemailはログへ出さない。Cloud Run Job化は別タスクとする。

```bash
python manage.py shell <<'PY'
from django.db import connection
from django.utils import timezone

table = connection.ops.quote_name('login_rate_limit_cache')
with connection.cursor() as cursor:
    cursor.execute(f'DELETE FROM {table} WHERE expires < %s', [timezone.now()])
    print(f'deleted={cursor.rowcount}')
PY
```

全レート制限を解除する`cache.clear()`は定期清掃には使わない。

## ヘルスチェック {#health}

Cloud Run の readiness / liveness probe 用に `/health` エンドポイントを提供する。

| 項目 | 値 |
|------|-----|
| パス | `/health` |
| メソッド | GET |
| 認証 | 不要 |
| レスポンス（正常） | `200 OK` / `{"status":"ok","db":"ok","cache":"ok"}` |
| レスポンス（DB 障害） | `503 Service Unavailable` / `{"status":"ng","db":"ng", ...}` |

### 設計方針

- **DB の疎通失敗は致命的**: 503 を返してロードバランサから外す。zombie プロセスへの誤ルーティングを防ぐ。
- **cache 失敗は無視**: cache が未設定でも生存判定したいので、`cache=ng` でも `status=ok` を維持する。
- **軽量実装**: DBは`connection.ensure_connection()`で確認し、cacheは専用LocMem aliasを往復する。
  probeごとにDatabaseCacheへINSERTしないため、Cloud SQLへの追加書き込みは発生しない。

### 動作確認

```bash
# ローカル
curl -i http://localhost:8015/health

# 本番（Cloud Run）
curl -i https://vrc-ta-hub.com/health
```

### Cloud Run probe 設定例

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8000
  initialDelaySeconds: 30
  periodSeconds: 10
  failureThreshold: 3
```

## 関連ドキュメント

- [セットアップ](setup.md)
- [静的ファイルの Cloudflare R2 同期手順](static_files_sync.md)
