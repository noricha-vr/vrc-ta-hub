# テスト方針

VRC技術学術ハブのテスト戦略と共有ヘルパーの使い方をまとめる。

## 基本方針

- **Django 標準の `TestCase` を使用する**（pytest-django は導入しない）
- テストファイルは各 Django アプリの `tests/` ディレクトリ配下に置く
  （例: `app/event/tests/`, `app/community/tests/`）
- 新規テスト追加時は、まず `app/tests/factories.py` を確認する

## テスト実行

```bash
# 通常suite（外向き通信を遮断し、live smoke / browser E2Eを除外）
scripts/run_tests.sh

# 特定アプリ
scripts/run_tests.sh event.tests

# 特定モジュール
scripts/run_tests.sh event.tests.test_notifications

# 特定クラス・メソッド
scripts/run_tests.sh \
  event.tests.test_notifications.NotifyOwnersOfNewApplicationTest.test_sends_email_to_each_owner
```

通常テストは `OfflineNetworkDiscoverRunner` で実行し、Unix socket と loopback
（`localhost` / `127.0.0.0/8` / `::1`）以外への DNS・socket 接続を拒否する。
CI には dummy credential だけを渡し、外向き通信が発生した時点でテストを失敗させる。

通常実行の正本は `scripts/run_tests.sh`。引数を指定した場合も内部で
`tests.offline_manage`、`OfflineNetworkDiscoverRunner`、`live_smoke` / `e2e` の除外を必ず付ける。
生の `python manage.py test` はこれらを省略できるため、通常テストの標準手順にはしない。

### 通信遮断境界の回帰確認

offline runner 自体の回帰テストも、通常は標準入口から実行する。

```bash
scripts/run_tests.sh website.tests.test_offline_runner
```

Docker の network namespace が返す遮断例外を確認するときだけ、offline monkeypatch を
使わない raw `DiscoverRunner` を `--network none` で実行する。再現用の image build と
コマンド、許容する例外型・errno の境界は
[Issue #529 の調査結果](research/issue-529-offline-network-none.md)を参照する。

### 外部連携テストの分類

| 分類 | タグ | 通信 | 対象 |
|------|------|------|------|
| Offline contract | `offline_external_api` またはタグなし | mock / fake / locmem / file backend のみ | X API、OpenRouter、Discord、OAuth、メール、画像、定期イベント、retry / idempotency |
| Live smoke | `live_smoke` + `external_api` | 実サービスへ接続 | Google Calendar、OpenRouter、YouTube / Google API |
| Browser E2E | `e2e` + `browser` | Django live serverと固定CDN | Playwrightの主要ユーザー導線 |

`tests.offline_manage` はDjango初期化前に通信遮断を開始し、test runnerでも
同じ境界を維持する。`external_api` は既存の実行環境との後方互換用で、実疎通の選択には
`live_smoke` を使う。旧 `external_api` 対象のうち、mock / fake / locmem /
file backend だけで完結するテストは `offline_external_api` へ移し、通常CIに含める。

主な分類は次のファイルから追跡できる。

- Offline contract: `twitter/tests/test_auto_tweet.py`,
  `event/tests/test_recurrence_rule_generation.py`,
  `event/tests/test_recurrence_idempotency.py`,
  `event/tests/test_generate_recurring_events_command.py`,
  `user_account/tests/`, `ta_hub/tests/test_resize_image.py`
- Live smoke: `event/tests/test_google_calendar.py`,
  `event/tests/test_recurrence_llm_generation.py`,
  `event/tests/test_generate_blog.py` の `@require_live_smoke` 対象メソッド、
  recurrence previewの自由記述ルール実疎通
- Browser E2E: `website/tests/e2e/test_user_journeys.py`

Live smoke は通常suiteから除外する。`@require_live_smoke(...)` は
`RUN_LIVE_SMOKE_TESTS=1` と対象サービスの実credentialが揃わない直接実行をskipする。
標準入口の `scripts/run_tests.sh --live-smoke ...` はflagを自動設定し、dummy / test /
placeholder 値やGoogle Calendar credentialファイル不足をcontainer起動前の設定エラーにする。

live smoke は必ず固定profileを指定する。値は現在のshell環境を優先し、未設定なら
git管理外の `.env.local` からprofileのallowlistにあるキーだけを読む。別ファイルを
使う場合は `LIVE_SMOKE_ENV_FILE` で指定する。

| Profile | 渡すcredential | 既定の対象 |
|---------|------------------|------------|
| `openrouter` | `OPENROUTER_API_KEY` | 定期日生成、PDFのみの記事生成 |
| `youtube` | `GOOGLE_API_KEY` | YouTube文字起こし取得 |
| `blog-generation` | `OPENROUTER_API_KEY`, `GOOGLE_API_KEY` | 動画を含む記事生成 |
| `google-calendar` | `GOOGLE_CALENDAR_ID`, `GOOGLE_CALENDAR_CREDENTIALS` | Calendar CRUD |

```bash
# profileの既定テストを実行
scripts/run_tests.sh --live-smoke openrouter

# 任意のtest labelへ絞る場合
scripts/run_tests.sh --live-smoke youtube \
  event.tests.test_generate_blog.TestGenerateBlog.test_get_transcript

# credential sourceを明示する場合
LIVE_SMOKE_ENV_FILE="$HOME/.config/vrc-ta-hub/live-smoke.env" \
  scripts/run_tests.sh --live-smoke google-calendar
```

`google-calendar` の `GOOGLE_CALENDAR_CREDENTIALS` には、repository/build context
外に置いたJSON鍵の絶対パス（例: `$HOME/.config/vrc-ta-hub/credentials.json`）を指定する。
`/app/...`、相対パス、repository内のファイル、外部symlinkからrepository内を指すパスは
image layerへの混入防止のため拒否する。

専用の `docker-compose.live-smoke.yml` は既存app containerを再利用せず、別の
Compose project・networkでsecret非同梱のイメージを再ビルドする。serviceには
`env_file` やhost source volumeを設定しない。credential値はshell argvに載せず、
host subprocess環境をclean-env化してprofileで許可した実credentialとDocker実行に必要な
最小限のhost変数だけを渡す。他profileのcredential形状envはCompose内の固定dummy値のままにする。
Google CalendarのJSON鍵は指定ファイル1個だけをread-only mountする。未知profile、missing /
dummy credential、存在しないJSON鍵はcontainer起動前に拒否する。

`scripts/run_tests.sh` はテスト名などの引数を渡した場合もoffline境界を適用する。
実疎通だけを上記 `--live-smoke <profile> [test label]` で明示し、通常実行と分離する。

## ブラウザ E2E テスト

Python Playwright と Django の `StaticLiveServerTestCase` を組み合わせ、実際の
Chromium で次の主要ユーザー導線を検証する。

1. トップページから集会一覧・集会詳細・公開済み発表を閲覧する
2. 公開フォームでログインし、アカウント設定を確認してログアウトする
3. 発表者が発表を申請し、主催者が承認した後、匿名状態で公開発表を閲覧する

### 依存関係の更新と導入

E2E 専用依存は `requirements-e2e.txt` に追加する。本体依存との整合を保つため、
`requirements.lock` を制約として専用 lock を生成する。

```bash
# E2E 専用 lock を再生成
uv pip compile requirements-e2e.txt --constraint requirements.lock -o requirements-e2e.lock
```

CI は `uv pip sync --system requirements.lock requirements-e2e.lock` で本体と
E2E の依存を同時に導入し、`python -m playwright install --with-deps chromium`
で Chromium とシステム依存を導入する。

### 実行

```bash
docker compose -f docker-compose.test.yml run --rm --build test /bin/sh -c \
  "pip install --no-deps -r /app/requirements-e2e.lock && \
   python -m playwright install --with-deps chromium && \
   python manage.py test website.tests.e2e.test_user_journeys \
     --tag=e2e --noinput --verbosity=2"
```

E2E テストには `@tag('e2e', 'browser')` を付ける。通常のテスト job は
`--exclude-tag=e2e` で除外し、Chromium を導入する CI 専用の `e2e` job で実行する。

各テストはブラウザコンテキストを分離する。Django のライブサーバー以外への HTTP
通信は、GET かつ resource type が stylesheet / script / font / image で、次の
バージョン固定 prefix に一致する UI 資産だけを許可する。

- Font Awesome 6.5.1 / 6.0.0-beta3（`cdnjs.cloudflare.com`）
- Bootstrap 5.3.3 / Bootstrap Icons 1.11.3（`cdn.jsdelivr.net`）

Google Fonts、PetiPeti、上記以外の外部画像、外部 API、Google Analytics、外部ページ
への通信は遮断する。テスト用画像は `TemporaryDirectory` と `FileSystemStorage` で
ローカル一時ストレージへ隔離する。許可した UI 資産の失敗記録は全画面スクリーンショット
の保存後にも再検査し、読み込み失敗時はテストを失敗させる。スクリーンショットと
Playwright trace は `test-results/e2e/` に保存され、CI 失敗時に artifact として
アップロードされる。

## 共有ヘルパー (`app/tests/factories.py`)

User / Community / Event / EventDetail を `setUp` で毎回手書きする重複を削減する
ための薄い factory 群。**新規テストはまずここから探す。** 足りない引数は
`**extra` で渡すか、本ファイルを拡張する。

### 提供関数

| 関数 | 用途 |
|------|------|
| `make_user(...)` | 通常ユーザー（Discord 未連携、モデル/通知/フォーム単体テスト向け） |
| `make_discord_linked_user(...)` | Discord 連携済みユーザー（画面テストで `DiscordAuthRequiredMiddleware` を通すため） |
| `make_community(...)` | Community。`owner` 指定で OWNER ロールの `CommunityMember` も作成 |
| `make_event(...)` | Event。`event_date` 未指定で `today + 7 days`（未来イベント） |
| `make_event_detail(...)` | EventDetail。`start_time` 未指定で親 event の `start_time` を引き継ぐ |

### 使用例

```python
from django.test import TestCase

from tests.factories import (
    make_community,
    make_event,
    make_event_detail,
    make_user,
)


class NotificationTest(TestCase):
    def setUp(self):
        self.owner = make_user(user_name="owner1", email="owner1@example.com")
        self.applicant = make_user(user_name="applicant1", email="applicant1@example.com")
        self.community = make_community(owner=self.owner, webhook_url="")
        self.event = make_event(self.community)
        self.event_detail = make_event_detail(self.event, applicant=self.applicant)
```

### キーワード引数の上書き

デフォルト値を上書きしたい時は名前付き引数か `**extra` で渡す:

```python
# 過去日付の Event を作りたい
make_event(community, event_date=date.today() - timedelta(days=7))

# weekdays / frequency をカスタムしたい
make_community(owner=user, weekdays=["Tue", "Thu"], frequency="Bi-weekly")

# 発表時刻・却下理由を指定したい
make_event_detail(
    event,
    applicant=user,
    status="rejected",
    start_time=time(22, 30),
    rejection_reason="テーマが集会の趣旨に合いません",
)
```

### import 路

テストランナーは `app/` を作業ディレクトリにして起動するため、
import 路は `from tests.factories import ...` になる
（`from app.tests.factories ...` ではない）。

## 既存テストの段階移行

既存テストファイルには `_make_user` / `_make_community` 等の同名ヘルパーが
個別定義されている場合がある。互換性のため一部は薄い wrapper として残し、
内部実装だけ `tests.factories` に委譲している。新規テスト追加時は
wrapper 経由ではなく `make_*` を直接 import すること。

## テスト追加時のチェックリスト

- [ ] テストファイルは対象アプリの `tests/` ディレクトリ配下に置いたか
- [ ] `setUp` で User/Community/Event/EventDetail を作るなら `tests.factories` を使ったか
- [ ] factories で足りない場合、本ファイルを拡張したか（個別ヘルパーを増やさない）
- [ ] mock / fakeだけで完結する外部連携テストは通常suiteで実行されるか
- [ ] 実サービスへ接続するテストは `@require_live_smoke(...)` を付けたか
- [ ] live smokeに必要なcredentialを対象サービスだけに限定したか
- [ ] Discord OAuth 必須画面のテストでは `make_discord_linked_user` を使ったか
