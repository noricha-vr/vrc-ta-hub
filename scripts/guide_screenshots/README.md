# 使い方ガイドの画像を撮り直す

架空のデータを一時 SQLite DB に作り、ローカルの画面を Playwright で撮影します。通常の開発 DB は使いません。スクリーンショットは PNG から WebP（品質 82）に変換します。

## 前提

Python 3.12、uv、Chromium が必要です。リポジトリ直下で準備します。

```bash
uv venv --python 3.12 .venv-guide
uv pip sync --python .venv-guide/bin/python requirements.txt requirements-e2e.txt
.venv-guide/bin/python -m playwright install chromium
```

Mac で mysqlclient をビルドできない場合は、SQLite 用にそのパッケージだけを除外します。元のロックファイルは変更しません。

```bash
sed '/^mysqlclient==/d' requirements.txt > /tmp/guide-requirements.txt
sed '/^mysqlclient==/d' requirements-e2e.txt > /tmp/guide-requirements-e2e.txt
uv pip sync --python .venv-guide/bin/python /tmp/guide-requirements.txt /tmp/guide-requirements-e2e.txt
.venv-guide/bin/python -m playwright install chromium
```

Playwright は `requirements-e2e.txt` の固定バージョンと一致させます。違うバージョンの場合は撮影前に停止します。ポート待ち受けと Chromium の起動が許可されたターミナルで実行してください。

## 実行

```bash
# 全ページを撮り直す（PC 幅 1280px。mobile=True のシナリオはスマホ幅 390px も）
GUIDE_PYTHON="$PWD/.venv-guide/bin/python" bash scripts/guide_screenshots/run.sh

# 一部だけ（複数指定も可）
GUIDE_PYTHON="$PWD/.venv-guide/bin/python" bash scripts/guide_screenshots/run.sh community/create
GUIDE_PYTHON="$PWD/.venv-guide/bin/python" bash scripts/guide_screenshots/run.sh settings/discord
```

`GUIDE_PYTHON` の既定値は `python3`。ポートの既定値は 8019（`GUIDE_PORT` で変更可）で、使用中なら空きポートを選びます。

`run.sh` は環境変数を初期化し、ダミーの API キー等だけを渡します。Fernet 鍵は実行ごとに生成します。一時設定は `from website.settings import *` を読み、DB を SQLite のファイルに置き換えます。`TESTING=1` は使いません。`migrate` → `seed_guide_demo` → サーバー起動 → 撮影の順に実行し、成功時・エラー時・中断時にサーバーを止めて一時 DB と設定を削除します。

Django のプロセスは既存の offline runner で外向き通信を遮断します。ブラウザはローカル画面とモックのほか、既存 UI の公開 CSS・フォント・Bootstrap 等の配信元だけを許可します。解析タグや外部 API は読み込みません。CSS・フォントの取得にはネット接続が必要です。

出力先は `app/guide/static/guide/images/<セクション>/<ページ名>-NN.webp`、スマホは `-NN-sp.webp` です。同じファイル名で上書きします。スマホ幅は `@scenario('community/create', mobile=True)` のように指定したページだけ撮ります（フォームやメニューなど操作の多い画面）。撮影枚数を減らした場合、不要になった旧番号の画像は参照を確認して削除してください。

## デモデータ

共通の土台（ユーザー・集会・イベント・招待）は `app/guide/management/commands/seed_guide_demo.py` にあります。ページの撮影に要る状態は、`app/guide/demo_seeds/<セクション>.py` に `seed(context)` を定義して足します（名前順に呼ばれます。`context` は `users`（役割→ユーザー）・`community`（主催者の集会）・`base_date` を持つ辞書）。セクションごとにファイルを分けるのは、複数人が同時にページを書いても衝突しないようにするためです。`app/guide/demo.py` の 4 人（主催者・スタッフ・発表者・一般）は架空の名前と `@example.com` のメールだけを使います。パスワードは `run.sh` が撮影のたびに作る使い捨てで（環境変数 `GUIDE_DEMO_PASSWORD`）、リポジトリには固定値を置きません。Discord 連携 ID は数字の実 ID と紛らわしくない `guide-demo-*` です。

日付は **2030-04-01 を基準に固定**します。実行日が変わっても画像内の予定が変わらないようにするためです。実際の開催予定ではありません。招待の期限も固定です。期限依存の画面を追加する場合は、この基準日と表示する状態を一緒に見直します。

コマンドは専用設定の一時 SQLite 環境以外では実行を拒否します。同じ DB での二度目の実行は既存デモデータを保持します。毎回新しい一時 DB を作る `run.sh` で内容を再現してください。

## シナリオを足す

セクションごとに `scenarios/<セクション>.py` を作り、ページ単位で登録します。登録名は目次と同じ path です。新しいファイルは自動検出されます。

```python
from capture import scenario

@scenario('community/edit')
def edit(capture):
    capture.login('owner')  # owner / staff / speaker / general
    capture.goto('/community/update/')
    capture.annotate('#id_name', 1)
    capture.shot(selector='form[enctype="multipart/form-data"]')
```

各ページは PC（と、mobile=True ならスマホ）それぞれ独立したブラウザコンテキストで実行します。`capture.mobile` で幅ごとに処理を変えられます。`login()` はメール入力の属するフォームを送信し、先に置かれた Discord ログインフォームとの混同を避けます。保存・テスト送信などの外部操作はシナリオに入れないでください。

## 注釈と伏せ字

```python
capture.annotate('#id_name', 1)
capture.annotate('#id_start_time', 2)
capture.mask('input[name=notification_webhook_url]')
capture.shot(selector='.card')  # 一意な要素に絞る
# または、ページ全体
capture.shot()
# または、文書上の座標で切り出す
capture.shot(clip={'x': 0, 'y': 100, 'width': 700, 'height': 450})
```

注釈は撮影直前に DOM に赤枠と、枠の右上に赤い丸の番号（1〜20）を重ねます。対象が複数ある、または非表示なら停止します。要素の切り出しでは番号のために上部に余白を足します。伏せ字は撮影ページ内だけの操作で、値・文字列を消して灰色の帯に置き換えます。DB には保存しません。ページ移動で注釈と伏せ字指定をリセットします。

## Discord の図を足す

`mocks/` に素の HTML + CSS で簡略図を作ります。外部アカウントや実データは読み込まず、右上等に「イメージ図」と入れます。

```python
capture.mock('discord-webhook.html')  # file:// で開く
capture.annotate('#new-webhook', 1)
capture.shot()
```

本文は `![操作と赤枠の位置を説明する alt](images/settings/discord-02.webp)` と書き、直後の段落に「① の『新しいウェブフック』を押します。（画面はイメージです）」のように説明を付けます。

## 撮影ガードと確認

撮影直前の表示テキストと入力値を検査し、次を見つけると画像を保存せず停止します。エラーに検知値は表示しません。

- `@example.com` 以外のメールアドレス
- 17〜19 桁の数字（Discord ID と紛らわしいもの）
- `https://discord.com/api/webhooks/0+/DUMMY` 以外の Webhook URL
- `sk-`、GitHub・Google・AWS 等の鍵形式、JWT、秘密鍵ヘッダー
- 値が残ったパスワード入力欄

伏せ字の対象は検査前に消します。ガードで実在の名前やあらゆる秘密を検出できるわけではありません。撮影後は全画像を見て、赤枠と番号・文字の読みやすさ・個人情報の不在を確認してください。

```bash
# 本文・目次・リンク・画像・ガード・デモ状態の検証
cd app
python manage.py test guide
```

成功時は `guide_screenshot`（画像パスと寸法）と `guide_capture_complete`（枚数と秒数）の JSON 行を標準出力に出します。`run.sh ... | tee /tmp/guide-capture.jsonl` で比較用に残せます。新シナリオ追加時・画面変更時に、期待枚数と寸法が変わっていないか確認してください。
