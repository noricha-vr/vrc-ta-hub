# 管理画面の UI/UX パターン

集会の主催者・スタッフ・運営がログインして使う画面（イベント管理、集会設定、発表の申請・編集、テンプレート、メンバー、アクセス解析など）の見た目と使い勝手の決まり。公開ページ（集会・イベント・発表の表示）と Django admin は対象外。

実装の手本は `app/event/templates/event/my_list.html`（イベント管理、#809）。新しい管理画面を作る時・既存の管理画面を触る時は、この文書の型に合わせる。

## 原則

1. **機能はそのまま、見え方を整える**: 見た目の作り替えで項目・送信先・権限・URL を変えない。変える時は別の Issue で決める
2. **今どの集会の何を触っているかを一番上で答える**: 見出しに集会名を必ず出す
3. **対応が要るものを先に見せる**: 承認待ち・未設定・警告は、一覧より上の 1 か所にまとめる
4. **普段使う操作だけを見せ、残りは畳む**: 1 つの行・カードに見せるボタンは 2〜3 個まで。残りは「…」メニューへ
5. **どのボタンで何が保存されるかを見せる**: 保存の範囲と未保存の状態を画面に出す
6. **まだ作っていないものは、作る場所へ 1 回で行ける**: 未設定は点線のボタンで設定画面へ直行させる
7. **色は Hub の配色だけ**: 主色 `var(--custom-primary)`（rgb(27,125,230)）と Bootstrap 5.3 の標準色。ライト固定（ダークモードは作らない）

## ページの骨組み

上から次の順に並べる。

| 順 | 部品 | 中身 |
|---|---|---|
| 1 | パンくず | `イベント管理 / 集会設定 / …`。イベント管理がトップ（`event:my_list`） |
| 2 | 見出し | 左: `画面名: 集会名` の h1。集会を切り替えられる画面は「管理中の集会」の小ラベル＋集会名 ▾（枠なし、マウスを乗せると背景色）。右: 二次ボタン（公開ページを見る ↗ など）＋主ボタン 1 つ |
| 3 | サブナビ | イベント / 発表履歴 / アクセス / 集会設定 / 運営だけの画面（「運営」バッジ）。今いる画面に `aria-current="page"`。スマホでは折り返さず横スクロール。本人用の画面（自分の発表申請など）は集会のサブナビに混ぜず、「個人用」の行に分ける |
| 4 | 要対応 | 承認待ち・警告を 1 つの箱にまとめ、その場で操作できるボタンを付ける。対応が要る数だけを「要対応 N」と数え、案内（info）は数えない。対応が要るものが無く案内だけなら見出しは「お知らせ」 |
| 5 | 本文 | 見出しで区切ったカードの並び（下の「一覧」「フォーム」） |

h1 を集会切替のボタンの中に入れない（読み上げで見出しが壊れる）。切替が見出しを兼ねる時は `visually-hidden` の h1 を別に置く。

## 一覧

- 「これから」「過去」のように、時間や状態の境目は見出し（`section-title`）で分ける。背景色の違いだけで区切らない
- 畳んだ残りは「残り N 件を表示（12/3〜12/17）」のように件数と範囲を書く。「もっと見る」だけにしない
- カード 1 枚 = 1 件。左に識別（日付・時刻・件数・状態）、右に操作
- 操作の階層: 主な操作 1〜2 個（`btn-sm btn-outline-secondary`）→ メニュー付きの操作（`X で告知 ▾`）→ 「…」（変更・削除などのまれな操作）
- カードの下の端に置く追加ボタン（「発表を追加」など）は、中身の有無で位置を変えない
- 状態や資料の有無は、文字の入ったチップ（`detail-chip`）で出す。文字の無いアイコンだけにしない

## フォーム

- 項目はまとまりごとのカードに分け、カードの見出しに名前を付ける（例: 日程 / 主催と連絡先 / 掲載内容 / 発表の受付 / 通知と連携）
- 一組の情報は隣に並べる（開始時刻・開催時間・曜日・開催周期など）
- 複数選択（曜日・タグ）は 1 行のチップ型チェックボックスにし、`fieldset` と `legend` で囲む
- 自由入力で書き方がぶれる欄には入力例を `placeholder` かヘルプに出す（例: 毎週 / 隔週 / 毎月第 2 土曜 / 不定期）
- 画像の入力欄の近くに、今の画像を小さく出す
- 長いフォームは、保存ボタンを画面下に固定したバー（stickybar）に置く。バーには「変更はありません」「未保存の変更があります」を出し、保存とキャンセル（戻り先へのリンク）を並べる
- 保存がカードごとに分かれる場合は、カードの見出しに「このカードだけ保存」と書き、変更があれば「未保存」を出す
- 説明は 1 文にして、選択肢の横に短い補足を置く。操作の前に長い説明を読ませない
- 条件によって使えない項目は消さずに無効表示にし、理由を書く

## 秘密の値

Webhook URL・API キー・トークンは、保存済みなら全部を表示しない。末尾 4〜8 文字だけを見せ、「変更」を押した時だけ入力欄を出す。画面共有・配信・スクリーンショットで漏れるため。

## 確認と削除

- ブラウザ標準の `confirm()` は使わない
- 削除はカードの中に確認の枠（`confirm-panel`、薄い赤）を開き、そこで初めて付随する選択肢（「以降のイベントもまとめて削除する」など）を出す。ボタンは「〇〇を削除する」（赤）と「キャンセル」
- 取り返しのつかない操作（集会の閉鎖・主催者の引き継ぎ）は「危険な操作」として画面の最後に置き、畳んでおく

## 未設定・空の状態

- 未設定のものは、通常のボタンの代わりに点線の枠のボタン（`setup-placeholder`）で「〇〇を設定する」「〇〇を作成」と出し、設定画面へ 1 回で移る
- 設定画面がセッションの集会を対象にする場合は、`community:switch` に `community_id` と `redirect_to` を POST してから移る（セッションに集会が無いと 404 になるため）
- 空の一覧には、次にやる操作を書く。その操作のボタンが画面に無い時は書かない
- 計測したい導線には `data-*` 属性を付け、GA4 のイベントで数える（例: `data-mylist-setup`）

## 申請の前と後

集会の登録申請と登録後の設定は、同じ画面の状態違いとして扱う。

| 状態 | 見せるもの | 見せないもの |
|---|---|---|
| 申請（登録前） | 集会情報、選ぶだけで済む設定（発表を受け付けるか・撮影・持ち時間・開始のずれ）、同意 | 手間のかかる設定（Discord 通知・VRCイベントカレンダー・X の告知テンプレート・メンバー・API キー）。何があるかだけ点線の枠で見せる |
| 承認待ち | 進み具合（入力 → 運営が確認 → 掲載）、「まだ公開されていない」こと、申請内容の編集 | 承認後の設定は見せるだけで触れない |
| 登録後 | 左の目次＋全部のまとまり、画面下の保存バー、危険な操作 | なし |

## 見た目の値

| 用途 | 値 |
|---|---|
| 主色 / 押下 | `var(--custom-primary)`（rgb(27,125,230)）/ rgb(22,100,184) |
| 主色の薄い背景（選択中・hover） | `#edf5fe` |
| 文字 / 補足 | `#212529` / `#586573`〜`#6c757d` |
| 枠線 | `#dce2e9` |
| 要対応の箱 | 枠 `#ead29f`、左の帯 `#d69620`、背景 `#fffcf4` |
| 削除の確認 | 枠 `#e7b9b9`、背景 `#fff6f6` |
| 強調のマーカー | `#feffd5` |
| 角丸 | カード .8rem / 箱 .75rem / ボタン・チップ .35〜.5rem |
| 文字 | Noto Sans JP。h1 は 1.25〜1.8rem、カード見出し 1rem 太字、補足 .75〜.875rem |

## 幅と操作

- PC 幅とスマホ幅（390px）の両方で、横スクロールせずに操作できること
- ボタンは折り返してよいが、文字は 1 つのボタンの中で折り返さない。メニューは `min-width: 12rem`
- 押せる部品にはキーボードのフォーカス表示を残す

## 作った後の点検

出す前に、デザイナーの目で次を確かめる。

- 余白が空洞になっていないか、同じ文字列が続いていないか
- 同じ役割のボタンが 2 か所にないか（上のパンくずと下の「戻る」など）
- 状態（未保存・承認待ち・未設定・空）がそれぞれ画面に出るか
- 件数・ページ送りが出ているか
- PC とスマホで実画面を撮って見比べたか

## 部品の使い方

第 0 波の共通基盤。管理画面は `ta_hub/admin_base.html` を継承する。公開ページは引き続き `ta_hub/base.html` を使う。CSS・JS は管理画面だけで読む。`base.html` の `title` ブロックは既定の `<title>` の中にあり、未指定なら従来のタイトルになる。`meta` を上書きするページは自分の `<title>` を維持する。

### ブロック

| ブロック | 用途 |
|---|---|
| `title` | ページの HTML タイトル（タグ自体は含めない） |
| `extra_css` / `extra_js` | CSS / JS の追加。必ず `{{ block.super }}` を先頭に残す |
| `admin_classes` | `.hub-admin` に足すクラス。イベント管理は `mylist` |
| `admin_breadcrumbs` | パンくず。先頭は `event:my_list` |
| `admin_header` | 共通のページ見出し |
| `admin_subnav` | 権限内のリンクだけを出すサブナビ |
| `messages` | 既定で既存の `ta_hub/messages.html` を表示 |
| `admin_attention` | 要対応・警告・案内 |
| `admin_layout` | 既定は `.admin-layout`。通常は上書きせず、以下の 2 ブロックを使う |
| `admin_toc` | 任意。`<aside class="admin-toc">…</aside>` を置く。未指定なら空 |
| `admin_content` | `.admin-content` 内の本文。カード本体はここに直接 HTML で書く |
| `admin_action_bar` | 保存バー。既存フォームを指すボタンを配置 |

```django
{% extends 'ta_hub/admin_base.html' %}
{% block title %}通知設定 - {{ community.name }}{% endblock %}
{% block admin_header %}
  {% include 'ta_hub/admin/includes/page_header.html' with title='通知設定' target=community.name %}
{% endblock %}
{% block admin_content %}
  <form id="notification-form" method="post" action="{{ notification_url }}" data-admin-dirty>
    {% csrf_token %}
    {% include 'ta_hub/admin/includes/form_errors.html' with form=form %}
    <section class="admin-card" aria-labelledby="notification-heading">
      {% include 'ta_hub/admin/includes/section_heading.html' with title='通知' heading_id='notification-heading' %}
      {% include 'ta_hub/admin/includes/form_field.html' with field=form.enabled %}
    </section>
  </form>
{% endblock %}
{% block admin_action_bar %}
  {% include 'ta_hub/admin/includes/action_bar.html' with actions=save_actions cancel_url=return_url %}
{% endblock %}
```

### include の引数

すべて `ta_hub/admin/includes/` 配下。引数は特記以外省略可。文字列は自動エスケープする。任意の本文 HTML を `safe` で出す仕組みはない。`*_template` は開発者がテンプレートに直書きするパスだけを渡し、リクエスト値・DB の文字列から選ばない。小部品は `only` を付けて必要な値だけ渡すことを推奨する。未定義の任意引数の評価で一覧全体の QuerySet を再評価することを避けられる。`*_template` で既存の分岐を引き継ぐ場合はコンテキストを共有するか、必要なオブジェクト・`csrf_token` を明示して渡す。

| ファイル | 引数と仕様 |
|---|---|
| `page_header.html` | **`title`**、`target`（`画面名: 対象名`）、`subtitle`、`actions`（`url` / `label` / `primary` の配列）。`switcher_template` があれば切替を表示し h1 は別に視覚非表示。`actions_template` は固有の操作を置く場合に `actions` より優先 |
| `community_switcher.html` | 既存コンテキストの `communities`、`active_community` を使用。既存の `community:switch` POST / CSRF / `community_id` / `redirect_to=event:my_list`、終了集会の無効表示を維持。イベント管理以外で異なる戻り先が必要なら専用の切替テンプレートを使う |
| `subnav.html` | `label`（既定「集会管理」）、`items`（`url` / `label` / `current` / `operator` の配列）。`current=True` で `aria-current="page"`、`operator=True` で運営バッジ。`links_template` はページ固有のリンクを置く場合に `items` より優先。部品内で権限を増やさず、呼出側でリンクを絞る |
| `section_heading.html` | **`title`**、`heading_id`、`description`、`save_scope`（「このカードだけ保存: 対象」）、`form_id`（フォームの変更状態）、`past=True`（過去の区切り） |
| `form_field.html` | **`field`**（Django の BoundField）。`django_bootstrap5.bootstrap_field` でラベル・必須表示・入力・ヘルプ・エラーを表示。入力名・ID・ウィジェットの属性を保持 |
| `form_errors.html` | **`form`**。全体と項目別のエラー、項目へのアンカー。hidden 項目のエラーはリンクなしで表示 |
| `action_bar.html` | **`actions`**（`form_id` / `scope` / `label` / `name` / `value` / `disabled` / `danger` の配列）、`cancel_url`、`cancel_label`（既定「キャンセル」）、`inline=True`（モーダルフッター等。固定しない）。ボタンは `form` 属性で指定したフォームだけを送信 |
| `empty_state.html` | **`title`**、`description`、`action_url` と `action_label`（両方ある時だけ導線を表示） |
| `confirm_panel.html` | **`panel_id` / `label` / `message` / `action_url` / `submit_label`**、`fields_template`（既存 hidden・選択肢等）、`submit_name` / `submit_value`、`open=True`（独立確認ページ用）、`cancel_url`（リンク。省略時は枠を閉じるボタン）。部品が POST フォームと CSRF を出すのでフォームの中に入れない |
| `status_chip.html` | **`label`**、`tone`（`success` / `warning` / `danger`、省略時は中立）、`icon`（装飾用のアイコンクラス）。文字を必ず出す |
| `setup_link.html` | **`label`**、`url`（リンク）、または `form_id`（既存フォームへの送信ボタン。`url` より優先）。`setup_type` / `community_id` は既存の `data-mylist-setup` / `data-community-id` が必要な時だけ指定。部品自身は計測しない |
| `secret_field.html` | **`field`**（BoundField）、**`label`**、`configured=True`（保存済み）、`suffix`（任意、最後の 4 文字だけ表示）、`help_text`。初期値・POST 値を `value` 属性に出さず、空の password 入力を使う。保存済みなら変更欄は閉じて無効。未設定・エラー時は開く。ウィジェットに特別な属性が必要な項目は専用に表示する |

`actions` 等の配列はビューで組み立てる。例えば保存バーは次のデータを渡す。既存の `action` の送信が必要なら `name` と `value` を指定する。

```python
save_actions = [
    {'form_id': 'community-form', 'scope': '集会情報', 'label': '集会情報を保存'},
    {'form_id': 'notification-form', 'scope': '通知', 'label': '通知を保存',
     'name': 'action', 'value': 'save_notifications'},
]
```

```django
{# ナビは権限判定済みのリンクの配列を渡す #}
{% include 'ta_hub/admin/includes/subnav.html' with items=admin_links %}
{% include 'ta_hub/admin/includes/empty_state.html' with title='登録はありません' action_url=create_url action_label='登録する' %}
{% include 'ta_hub/admin/includes/status_chip.html' with label='承認待ち' tone='warning' %}
{% include 'ta_hub/admin/includes/setup_link.html' with url=settings_url label='通知を設定する' %}
{% include 'ta_hub/admin/includes/secret_field.html' with field=form.webhook_url label='通知先' configured=has_webhook suffix=webhook_suffix %}
```

秘密値を含むカード全体のフォームでは、閉じた欄は disabled となり送信されない。**送信されなかった項目をビューが空として保存する場合は、そのまま導入しない。** 既存の通知専用フォームを保ち、変更時だけそのフォームを送る、または呼出側で非変更を保持する。秘密値自体を hidden に移して解決しない。伏せ字表示は新規キーの一度だけの表示・コピー機能を代替するものではなく、そのような画面では明示操作を別に作る。

### JS の契約

- フォームに一意の `id` と `data-admin-dirty` を付けた場合だけ変更検知する。`data-admin-form-status="その id"` に「変更はありません」または「未保存の変更があります」を出す。`data-admin-scope="通知"` を付けると先頭に範囲を表示する。フォーム外の `form` 属性付き入力、チェック、複数選択、ファイル、reset も対象。
- 値はフォームごとにメモリ内で比較するだけ。入力値を計測・ログ・ストレージ・追加の通信に出さない。保存ボタンは個別の標準送信だけで、一括連続 POST はしない。既存の Ajax 保存がある場合は、成功が確認できた時だけ `form.dispatchEvent(new Event('admin:saved'))` で基準を更新する。submit しただけでは保存済みにしない。
- 開くボタンに `data-confirm-panel="確認枠の id"`、`aria-controls="同じ id"`、`aria-expanded="false"` を付ける。キャンセル・Escape で閉じ、起点へフォーカスを戻す。起点が閉じた dropdown 内なら、そのメニューを開くボタンへ戻す。独立 URL では `open=True` と `cancel_url` を指定する。
- `secret_field` の変更欄は開いた時だけ入力を有効化する。保存済みの値を復元・再表示しない。
- 固定バーは ResizeObserver で実高を測り、末尾の余白と入力・エラーへのスクロール余白に反映する。モーダルでは `inline=True` を使う。ページ全体の固定バーは 1 本だけ置く。

```django
<button type="button" data-confirm-panel="delete-item" aria-controls="delete-item" aria-expanded="false">削除…</button>
{% include 'ta_hub/admin/includes/confirm_panel.html' with panel_id='delete-item' label='削除の確認' message='この項目を削除しますか？' action_url=delete_url submit_label='この項目を削除する' %}
```

### CSS クラス

すべて `.hub-admin` 内だけに適用する。色はこの文書の「見た目の値」を正本とする。`--admin-primary` / `--admin-primary-hover` / `--admin-primary-soft`、`--admin-text` / `--admin-muted` / `--admin-border`、`--admin-attention-*` / `--admin-danger-*` / `--admin-marker`、`--admin-radius-*`、`--admin-space-1`〜`6` を定義している。

| クラス | 用途 |
|---|---|
| `hub-admin` | 管理画面のスコープ。`admin_base` が付与 |
| `admin-layout` / `admin-content` / `admin-toc` | 本文と任意の左目次。狭幅では一列 |
| `admin-page-header` / `admin-page-heading` / `admin-header-actions` / `admin-muted` | ページ見出し・操作・補助文 |
| `admin-subnav` / `mylist-nav` | サブナビ。スマホではナビ内だけを横スクロール |
| `admin-card` / `admin-section-heading` / `section-title` / `past-section` | カード・h2・一覧の区切り |
| `admin-form-field` / `admin-form-errors` | 入力・エラー一覧 |
| `action-required` / `action-required-row` / `action-required-text` | 要対応の箱・行・本文 |
| `event-card` / `event-summary` / `event-date` / `event-time` / `event-meta` / `event-actions` / `event-footer` | 一覧カードの識別・操作・末尾導線 |
| `presentation-row` / `presentation-heading` / `presentation-text` / `presentation-theme` / `presentation-speaker` / `presentation-actions` | 一覧内の発表と操作 |
| `detail-chip` / `setup-placeholder` / `confirm-panel` | 状態・未設定・確認 |
| `admin-action-bar` / `stickybar` / `admin-action-status` / `admin-action-buttons` | 操作バー・固定・状態・ボタン |
| `admin-dirty-status` / `is-dirty` | 未保存表示（JS が切替） |
| `admin-empty-state` / `admin-secret-field` / `admin-secret-status` / `admin-secret-edit` | 空状態・秘密値 |
| `mylist` / `mylist-header` / `mylist-header-actions` / `community-selector` / `community-selector-button` / `community-label` / `community-name` | 手本の一覧幅・集会切替。既存の寸法を保持 |

`vket-banner*` はイベント管理固有のバナーの見た目を保持するため同じ CSS に移している。共通の管理カードとして流用しない。公開ページの `body` / `.btn` / `.card` / `.form-text` は上書きしない。
