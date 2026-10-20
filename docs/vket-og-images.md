# コラボのサムネイル設定

管理画面の対象コラボの `settings_json` に次の `og_images` を追加する。
既存の設定キーは残し、対象のコラボ名・スラッグを確認してから保存する。
この変更はデータベースの設定を自動更新しない。

```json
{
  "og_images": {
    "apply": "vket/og/vket-2026-winter/apply.jpg",
    "status": "vket/og/vket-2026-winter/status.jpg",
    "notice_base": "vket-2026-winter"
  }
}
```

`apply` / `status` は static の相対パス。`notice_base` は `app/vket/og_assets/` 内のディレクトリ名。
背景の `notice-base.png`（1200×630）と、描画位置 `text_box`・文字色 `color` を指定する
`notice-base.json` を配置する。描画には同梱の OFL フォントを使用する。
設定がない・不正な場合は共通画像を使用する。`notice_base` が無いコラボのお知らせは、署名付きリンクでも従来の `notice_og_image` 設定（無ければ共通画像）を使う。

未ログインの参加登録・参加状況・お知らせページで meta を確認する。
個別のお知らせタイトルは、そのコラボのお知らせへの正しい署名付きリンクのときだけ公開される。
お知らせには下書きが無いため `sent_at` は見ない。下書きコラボは対象外。本文や宛先は匿名シェルに出ない。
管理画面のリマインド文をコピーすると署名付きのリンクを取得できる。
署名はタイトルの公開用であり、ログイン後の閲覧権限や受信者判定には使用しない。
古い署名なしのリンクは一覧のサムネイルを表示する。タイトル変更時は画像 URL の `v` が変わる。

画像レスポンスは公開キャッシュ（24時間）。一度配布したタイトル画像はキャッシュに残り得る。
背景・フォント更新後の外部プレビューの更新は、共有先のキャッシュにも依存する。
画像生成失敗時は共通画像へ転送し、`vket_og_render_failed` を警告ログに記録する。
ログにはコラボ ID・お知らせ ID のみを含み、タイトル・本文・署名は含めない。
リリース後の確認では、このログの件数と画像エンドポイントの 200 / 302 / 404 を確認する。

## Hub 全体のサムネイル

`og_images.stage_register` と `og_images.detail` には、それぞれステージ登録と
コラボ詳細の static 相対パスを指定できる。例えば次の設定を既存の `og_images` に追加する。

```json
{
  "stage_register": "vket/og/vket-2026-winter/stage-register.jpg",
  "detail": "vket/og/vket-2026-winter/detail.jpg"
}
```

未設定・不正なパスは共通画像へフォールバックする。ステージ登録は匿名 GET でも
ログイン案内とサムネイルを表示する。POST の登録処理には引き続きログインが必要。
詳細の公開範囲は従来どおり。

Hub の固定画像は `app/ta_hub/static/ta_hub/og/`、コラボ一覧は
`app/vket/static/vket/og/list.jpg` に置く（1200×630 JPEG）。
集会・発表申込み・ガイドの文字入り背景は `app/ta_hub/og_assets/` の
`community` / `lt-apply` / `guide` に `base.png` と `base.json` を置く。
共有フォントとライセンスは `app/ta_hub/og_assets/fonts/`。

公開画像エンドポイントは `/og/community/<pk>.png`、`/og/lt-apply/<pk>.png`、
`/og/guide/<path>.png`。集会は承認済み、発表申込みはさらに受付中のみを対象にする。
ガイドは既存の読み込み処理で検証したページのみで、任意の文字は受け付けない。
キャッシュキーは背景と保存済みの文字列から作り、クエリの `v` は無視する。
描画対象の種類は公開集会数・ガイドファイル数に限られ、履歴を含むプロセス内キャッシュも
64 件を上限にする。タイトルの変更時は画像 URL の `v` が変わる。

発表申込みの匿名 GET は公開案内を返し、ログイン後は従来の申込みフォームを表示する。
案内は noindex、private / no-store、Vary: Cookie。集会ポスターがあれば従来の画像を使う。

観測は画像エンドポイントの 200 / 302 / 404 と `hub_og_render_failed` の件数で行う。
失敗ログは ID のみを含み、ガイドの ID は検証済みパスのハッシュとする。
リリースの 7 日後に、アクセスログとこの警告の有無を確認する。
例: `gcloud logging read 'textPayload:"hub_og_render_failed" OR jsonPayload.event:"hub_og_render_failed"' --project=<project> --freshness=7d`。
