# テスト方針

VRC技術学術ハブのテスト戦略と共有ヘルパーの使い方をまとめる。

## 基本方針

- **Django 標準の `TestCase` を使用する**（pytest-django は導入しない）
- テストファイルは各 Django アプリの `tests/` ディレクトリ配下に置く
  （例: `app/event/tests/`, `app/community/tests/`）
- 新規テスト追加時は、まず `app/tests/factories.py` を確認する

## テスト実行

```bash
# 全テスト
docker compose exec vrc-ta-hub python manage.py test

# 特定アプリ
docker compose exec vrc-ta-hub python manage.py test event

# 特定モジュール
docker compose exec vrc-ta-hub python manage.py test event.tests.test_notifications

# 特定クラス・メソッド
docker compose exec vrc-ta-hub python manage.py test event.tests.test_notifications.NotifyOwnersOfNewApplicationTest.test_sends_email_to_each_owner
```

外部 API 依存テストは `@tag('external_api')` でマークされており、CI からは除外される。

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
- [ ] 外部 API を叩くテストは `@tag('external_api')` を付けたか
- [ ] Discord OAuth 必須画面のテストでは `make_discord_linked_user` を使ったか
