# 集会の活動監視と自動非表示

## 結論

ローカルMacから2週間に1回、xAI Responses APIの `x_search` を使ってX上の活動を確認します。X APIの開発者契約やXのアクセストークンは使いません。

ただし、「投稿が見つからない」だけでは終了を断定できないため、次の条件をすべて満たす場合だけ一覧から自動的に非表示にします。

1. Hub上の集会情報が90日以上更新されていない
2. 公式Xアカウントまたは登録ハッシュタグをGrokで検索しても90日以内の活動証拠がない
3. その判定が7日以上間隔を空けて2回連続した
4. 判定信頼度が0.75以上
5. 本番サーバーが、非表示直前に同じ条件を再検証できた

明示的な終了告知は信頼度0.90以上かつ公式Xアカウントの個別投稿URLが必要です。第三者の「終了したらしい」という投稿だけでは非表示にしません。

## なぜDBの未来イベントを活動証拠にしないのか

このプロジェクトは定期ルールから将来の `Event` を自動生成します。そのため、未来の日付がDBに存在しても、実際に開催された証明にはなりません。監視APIは過去・次回予定日をGrokの同名イベント識別用ヒントとして返しますが、有効性判定には使用しません。

## 構成

- 本番Django
  - `GET /community/activity-monitor/`: 90日以上更新されていない候補を返す
  - `POST /community/activity-monitor/`: 条件を再検証し `Community.end_at` だけを設定する
  - 専用の `COMMUNITY_ACTIVITY_MONITOR_TOKEN` で保護する
- ローカルMac
  - `scripts/community_activity_monitor.py`
  - xAI Responses API + `x_search`
  - 連続判定をローカルJSONへ保存
  - Discord Webhookへ結果を通知
  - launchdで14日ごとに実行

自動処理は将来イベント、定期ルール、Googleカレンダー予定を削除しません。誤判定時は既存の「終了した集会」画面から再開できる、復元優先のソフトアーカイブです。不要データの削除は、確認後に既存の管理者クリーンアップを手動実行してください。

## Grok Buildとの関係

Grok Build CLIはコード作成や保守には使えますが、定期実行の検索基盤としては使いません。自動処理は機械可読JSON、検索期間の固定、引用URLの検証、APIエラー処理が必要なため、xAI Responses APIの `x_search` を直接利用します。モデルは既定で `grok-4.5` に固定し、環境変数で変更できます。

## 1. 本番環境の設定

専用トークンを生成します。

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

生成値を本番Cloud Runへ設定します。

```text
COMMUNITY_ACTIVITY_MONITOR_TOKEN=<生成した値>
COMMUNITY_ACTIVITY_INACTIVE_DAYS=90
COMMUNITY_ACTIVITY_REQUIRED_CHECKS=2
COMMUNITY_ACTIVITY_MIN_INACTIVE_CONFIDENCE=0.75
COMMUNITY_ACTIVITY_EXPLICIT_END_CONFIDENCE=0.90
```

`REQUEST_TOKEN` とは別の値にしてください。専用トークンが未設定の場合、監視APIは常に401を返します。

## 2. ローカル環境の設定

```bash
mkdir -p ~/.config/vrc-ta-hub
chmod 700 ~/.config/vrc-ta-hub
cat > ~/.config/vrc-ta-hub/community-activity.env <<'EOF'
VRC_TA_HUB_ACTIVITY_TOKEN=<本番と同じ専用トークン>
XAI_API_KEY=<xAI APIキー>
COMMUNITY_ACTIVITY_DISCORD_WEBHOOK_URL=<管理者通知用Discord Webhook>

# 任意
COMMUNITY_ACTIVITY_XAI_MODEL=grok-4.5
COMMUNITY_ACTIVITY_INACTIVE_DAYS=90
COMMUNITY_ACTIVITY_REQUIRED_CHECKS=2
COMMUNITY_ACTIVITY_MIN_CHECK_INTERVAL_DAYS=7
COMMUNITY_ACTIVITY_MAX_CHECK_GAP_DAYS=35
COMMUNITY_ACTIVITY_MIN_INACTIVE_CONFIDENCE=0.75
COMMUNITY_ACTIVITY_EXPLICIT_END_CONFIDENCE=0.90
COMMUNITY_ACTIVITY_MAX_CANDIDATES=200
# COMMUNITY_ACTIVITY_EXCLUDED_IDS=12,34
# PYTHON_BIN=/absolute/path/to/.venv/bin/python
EOF
chmod 600 ~/.config/vrc-ta-hub/community-activity.env
```

依存ライブラリは既存の `requests` だけです。ローカルでプロジェクトのPython環境を使う場合は、環境ファイルの `PYTHON_BIN` にその絶対パスを指定します。

## 3. 初回ドライラン

既定では本番変更も状態保存もDiscord通知も行いません。

```bash
set -a
. ~/.config/vrc-ta-hub/community-activity.env
set +a
python scripts/community_activity_monitor.py
```

特定の集会だけ確認する場合:

```bash
python scripts/community_activity_monitor.py --community-id 123
```

ドライラン結果もDiscordへ送る場合:

```bash
python scripts/community_activity_monitor.py --notify-dry-run
```

最初の数件を見て、イベント名・Xアカウント・ハッシュタグの同定が正しいことを確認してください。

## 4. 手動で適用テスト

```bash
scripts/run_community_activity_monitor.sh --community-id 123
```

初回の活動なし判定では警告と状態保存だけです。7日以上後の2回目でも同じ判定になった場合に限り、`end_at` を設定して公開一覧と公開APIから非表示にします。

状態ファイルは既定で次に保存されます。

```text
~/.local/state/vrc-ta-hub/community-activity.json
```

状態ファイルを削除・破損した場合は連続回数が0に戻るため、早すぎる非表示ではなく、非表示が遅れる側に倒れます。

## 5. launchdで2週間ごとに実行

```bash
REPOSITORY_PATH="$(pwd)"
HOME_PATH="$HOME"
mkdir -p ~/Library/LaunchAgents ~/Library/Logs
sed \
  -e "s|__REPOSITORY_PATH__|$REPOSITORY_PATH|g" \
  -e "s|__HOME__|$HOME_PATH|g" \
  launchd/com.vrc-ta-hub.community-activity-monitor.plist.example \
  > ~/Library/LaunchAgents/com.vrc-ta-hub.community-activity-monitor.plist

plutil -lint ~/Library/LaunchAgents/com.vrc-ta-hub.community-activity-monitor.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.vrc-ta-hub.community-activity-monitor.plist 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vrc-ta-hub.community-activity-monitor.plist
```

すぐに動作確認する場合:

```bash
launchctl kickstart -k gui/$(id -u)/com.vrc-ta-hub.community-activity-monitor
```

ログ:

```text
~/Library/Logs/vrc-ta-hub-community-activity-monitor.log
~/Library/Logs/vrc-ta-hub-community-activity-monitor.error.log
```

## Discord通知

次の結果をまとめて通知します。

- 活動中を確認
- 1回目の活動なし警告
- 2回連続で自動非表示
- 判定不能
- xAI、Hub API、Discordのエラー
- xAIレスポンスが返した実コスト

`allowed_mentions` を空にしているため、集会名やモデル出力に `@everyone` が含まれてもメンションは発火しません。

## 誤判定対策

- 集会情報とX投稿をプロンプト内の「信頼できないデータ」と明示し、投稿内の命令を無視させる
- xAIが実際に `x_search` を実行したことをレスポンスから確認する
- `from_date` / `to_date` をツール側に設定し、90日範囲を強制する
- 活動中・終了判定は、xAIの引用一覧にも存在する個別X投稿URLだけを証拠として採用する
- 公式アカウントを先に検索し、第三者投稿だけでは終了扱いにしない
- 低信頼、ID不一致、引用なし、検索エラーはすべて連続判定をリセットする
- 本番DBへローカルから直接接続しない
- 本番側でメタデータ更新日、公開状態、識別子、信頼度、連続回数を再検証する
- 自動処理は `end_at` だけを変更し、削除はしない

## 費用の目安

X Searchは検索呼び出し回数とモデル入出力トークンで課金されます。100件を1件1検索として年26回実行した場合、検索ツール部分は概算で年13ドルです。公式アカウント検索とハッシュタグ検索の両方が必要な集会ばかりなら、上限寄りで年26ドル程度に加えてモデルのトークン料金がかかります。

活動中の投稿を一度確認した集会は、その投稿が90日範囲内にある間はローカルキャッシュを使うため、通常は毎回全件を再検索しません。

## 制約

- X上に一切告知しない集会は自動判定できません。Xアカウントまたは固有ハッシュタグがない集会は `unknown` として残します。
- 「投稿がない」は終了の証明ではありません。この実装は90日・2回連続・Discord通知・復元可能な非表示という保守的な運用です。
- X検索品質やモデル挙動は将来変わり得ます。モデル名は環境変数で更新できますが、変更後は必ずドライランで確認してください。
