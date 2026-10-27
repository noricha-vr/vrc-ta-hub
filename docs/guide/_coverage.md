利用者が触る画面と、説明している使い方ガイドを照合するための表です。
画面・URL・入口・権限を足したり変えたりしたら、この表と対応するガイドを直します。
ガイド本文で説明を確かめ、「記載あり」か、理由を添えた「対象外」にそろえます。

# 画面とガイドの対応表

確認日: 2026-10-27。URL の `<id>`・`<token>` などは可変部分です。
主催者・スタッフは対象集会のメンバーを指し、Hub 運営とは異なります。
ボタンで行う POST 操作も含めます。「記載あり」はガイドの説明との対応を示し、実操作の検証結果ではありません。

このファイルと `_review.md` は保守用の内部資料です。
`AGENTS.md` と `_` で始まるファイルは公開されず、`/guide/_coverage/` と `/guide/_review/` は 404 になります。

| 区分 | 画面 | URL | 使う人 | どこから開くか | ガイドのページ | 状態 |
|---|---|---|---|---|---|---|
| アカウント | ログイン | `/account/login/` | 未ログインの利用者 | ヘッダー「ログイン」 | [登録してログインする](account/login.md) | 記載あり |
| アカウント | 新規登録 | `/account/register/` | アカウントを作る人 | ヘッダー「登録」 | [登録してログインする](account/login.md) | 記載あり |
| アカウント | アカウント設定 | `/account/settings/` | ログインユーザー本人 | アカウントメニューの表示名。リンクの補足表記は「アカウント設定」 | [ユーザー情報を変更する](account/profile.md)、[Discordと連携する](account/discord.md) | 記載あり |
| アカウント | ユーザー情報の編集 | `/account/update/` | ログインユーザー本人 | アカウント設定「プロフィールと認証」→「ユーザー情報を編集」→「表示情報」「連絡先」「VRChat 情報」→画面下「ユーザー情報を保存」 | [ユーザー情報を変更する](account/profile.md) | 記載あり |
| アカウント | ユーザー名の変更 | `/account/user_name_change/` | ログインユーザー本人 | 独立したメニュー導線なし。通常は「ユーザー情報を編集」で変更。専用画面は「ユーザー名を変更」で保存 | [ユーザー情報を変更する](account/profile.md) | 記載あり |
| アカウント | パスワードの変更 | `/account/password_change/` | ログインユーザー本人 | アカウント設定「プロフィールと認証」→「パスワードを変更」→フォーム下「パスワードを変更」 | [パスワードを設定・変更する](account/password.md) | 記載あり |
| アカウント | ログアウト | `/account/logout/` | ログインユーザー | アカウントメニュー／アカウント設定「ログアウト」 | [登録してログインする](account/login.md) | 記載あり |
| アカウント | Discord 連携の案内 | `/account/discord-required/` | Discord 未連携のログインユーザー | 条件に該当すると自動表示。「Discord連携」の案内 | [Discordと連携する](account/discord.md) | 記載あり |
| アカウント | アカウント連携 | `/accounts/3rdparty/` | ログインユーザー本人 | アカウント設定「プロフィールと認証」→「連携を管理」と書かれた「Discord連携」の行 | [Discordと連携する](account/discord.md) | 記載あり |
| アカウント | Discord 連携の解除 | `/account/social/<id>/disconnect/` | 連携アカウントの本人。使用可能なパスワードが必要 | アカウント連携「解除」→「危険な操作」の確認欄→「解除する」 | [Discordと連携する](account/discord.md) | 記載あり |
| アカウント | パスワードの初回設定 | `/accounts/password/set/` | ログイン中でパスワード未設定の人 | Discord 連携の解除時、未設定なら移動 | [パスワードを設定・変更する](account/password.md) | 記載あり |
| アカウント | パスワード再設定の申請 | `/accounts/password/reset/` | パスワードを忘れた人 | ログイン画面「パスワードをお忘れですか？」 | [パスワードを設定・変更する](account/password.md) | 記載あり |
| アカウント | パスワード再設定メールの送信案内 | `/accounts/password/reset/done/` | 再設定を申請した人 | 再設定申請後に表示 | [パスワードを設定・変更する](account/password.md) | 記載あり |
| アカウント | メールのリンクからパスワード再設定 | `/accounts/password/reset/key/<uid>-<key>/` | 有効な再設定リンクを受け取った人 | 再設定メール内のリンク | [パスワードを設定・変更する](account/password.md) | 記載あり |
| アカウント | パスワード再設定完了 | `/accounts/password/reset/key/done/` | 再設定を完了した人 | 再設定後に表示 | [パスワードを設定・変更する](account/password.md) | 記載あり |
| アカウント | メールアドレス管理 | `/accounts/email/` | ログインユーザー本人 | 独立したアカウント設定のリンクはなし。通常の変更入口は「ユーザー情報を編集」 | [ユーザー情報を変更する](account/profile.md) | 記載あり |
| アカウント | 確認メールの送信案内 | `/accounts/confirm-email/` | メール確認を待っている人 | 登録・未確認メールでのログイン後に表示 | [登録してログインする](account/login.md) | 記載あり |
| アカウント | メールアドレス確認 | `/accounts/confirm-email/<key>/` | 有効な確認リンクを受け取った人 | 確認メール内のリンク | [登録してログインする](account/login.md)、[ユーザー情報を変更する](account/profile.md) | 記載あり |
| アカウント | 本人確認のための再認証 | `/accounts/reauthenticate/` | 再認証を求められたログインユーザー | 認証処理からの誘導。共通メニューにはなし | [登録してログインする](account/login.md) | 記載あり |
| アカウント | 利用停止アカウントの案内 | `/accounts/inactive/` | 利用停止と判定された人 | 認証処理から表示 | [登録してログインする](account/login.md) | 記載あり |
| アカウント | 外部アカウント登録の追加情報入力 | `/accounts/3rdparty/signup/` | 外部認証後、登録を完了する人 | Discord 認証後、追加情報が必要な場合 | [登録してログインする](account/login.md) | 記載あり |
| アカウント | 外部ログインのキャンセル案内 | `/accounts/3rdparty/login/cancelled/` | 認証をキャンセルした人 | 外部認証処理から表示 | [登録してログインする](account/login.md) | 記載あり |
| アカウント | 外部ログインのエラー案内 | `/accounts/3rdparty/login/error/` | 外部認証に失敗した人 | 外部認証処理から表示。「ログインページに戻る」 | [登録してログインする](account/login.md) | 記載あり |
| アカウント | 標準のパスワード変更画面 | `/accounts/password/change/` | ログインユーザー本人 | 標準認証の入口。アカウント設定は独自画面へリンク | [パスワードを設定・変更する](account/password.md) | 記載あり |
| アカウント | 標準のログアウト確認 | `/accounts/logout/` | 認証ライブラリのログアウト入口を開いた人 | 共通メニューは `/account/logout/` を使用 | [登録してログインする](account/login.md) | 記載あり |
| アカウント | 旧ログイン入口 | `/accounts/login/` | — | 公開ログインへ転送 | — | 対象外（独立画面なし） |
| アカウント | 旧登録入口 | `/accounts/signup/` | — | 新規登録へ転送 | — | 対象外（独立画面なし） |
| アカウント | 旧外部登録入口 | `/accounts/social/signup/` | — | 新しい外部登録 URL へ転送 | — | 対象外（互換転送） |
| アカウント | 旧アカウント連携入口 | `/accounts/social/connections/` | — | 新しい連携 URL へ転送 | — | 対象外（互換転送） |
| アカウント | 旧外部ログインキャンセル入口 | `/accounts/social/login/cancelled/` | — | 新しい案内 URL へ転送 | — | 対象外（互換転送） |
| アカウント | 旧外部ログインエラー入口 | `/accounts/social/login/error/` | — | 新しい案内 URL へ転送 | — | 対象外（互換転送） |
| アカウント | Discord 認証開始 | `/accounts/discord/login/` | 登録・ログイン・連携を行う人 | 「Discordでログイン」「Discordで登録」「Discord連携」 | [登録してログインする](account/login.md)、[Discordと連携する](account/discord.md) | 記載あり |
| 内部処理 | Discord 認証の戻り先 | `/accounts/discord/login/callback/` | 認証処理 | Discord 認証から自動遷移 | — | 対象外（認証コールバック） |
| 内部処理 | ログインコード確認 | `/accounts/login/code/confirm/` | 対応する認証段階にいる人のみ | 通常メニューなし | — | 対象外（コード認証用の補助処理） |
| 集会 | 集会一覧 | `/community/list/` | 未ログインを含む閲覧者 | ヘッダー／フッター「集会一覧」 | [集会と発表を探す](browse/find.md) | 記載あり |
| 集会 | 集会詳細 | `/community/<id>/` | 承認済み集会は誰でも。未承認は `is_superuser` | 集会一覧の集会名・ポスター／イベント管理「公開ページを見る ↗」 | [集会と発表を探す](browse/find.md) | 記載あり |
| 集会 | 掲載・審査基準 | `/community/criteria/` | 誰でも | フッター「掲載・審査基準」／集会登録フォーム | [集会と発表を探す](browse/find.md) | 記載あり |
| 集会 | 終了した集会のアーカイブ | `/community/archive/` | 誰でも | 集会一覧「アーカイブ」 | [集会と発表を探す](browse/find.md) | 記載あり |
| 集会 | 集会の登録 | `/community/create/` | ログインユーザー | アカウント設定「管理中の集会」→「新しい技術・学術系集会を始める」 | [集会を登録する](community/create.md) | 記載あり |
| 集会 | 集会設定 | `/community/settings/` | 所属集会の主催者・スタッフ | アカウント設定「管理中の集会」の対象集会の「設定」／イベント管理「集会設定」 | [集会設定を開く](community/settings.md) | 記載あり |
| 集会 | 集会情報の編集 | `/community/update/` | 選択中の集会を編集できるメンバー | 集会設定の入力欄で直接編集（旧編集 URL も同じ設定フォーム） | [集会情報を編集する](community/edit.md) | 記載あり |
| 集会 | 管理する集会の切り替え | `/community/switch/`（POST） | 切り替え先の集会メンバー | イベント管理「管理中の集会」の集会名選択／アカウント設定「管理中の集会」の集会名・「設定」 | [管理する集会を切り替える](community/switch.md) | 記載あり |
| 集会 | 集会の閉鎖確認・実行 | `/community/close/<id>/`（POST） | 主催者、または `is_superuser` | 集会設定「危険な操作」を開く→「集会を閉鎖する」→確認欄「集会を閉鎖する」 | [集会を閉鎖・再開する](community/close.md) | 記載あり |
| 集会 | 集会情報を確認して再開 | `/community/reopen/<id>/` | 主催者、または `is_superuser` | アカウント設定「管理中の集会」の終了した集会「集会を再開」 | [集会を閉鎖・再開する](community/close.md) | 記載あり |
| 集会 | 集会の再開完了 | `/community/reopen/<id>/complete/` | 主催者、または `is_superuser` | 再開後。「開催予定・開催周期を登録する」 | [集会を閉鎖・再開する](community/close.md) | 記載あり |
| 集会 | 活動停止の通報 | `/community/<id>/report/`（POST） | 未ログインを含む閲覧者。対象は承認済み集会 | 集会詳細「この集会が開催されていないかもしれない」→「通報する」 | [活動停止を通報する](community/report.md) | 記載あり |
| 集会 | 集会ポスターのダウンロード | `/community/<id>/poster/download/` | 誰でも。承認済み・転載許可あり・画像あり | 集会詳細「集会を紹介するためのポスターの転載可」 | [集会と発表を探す](browse/find.md) | 記載あり |
| 集会 | 承認待集会一覧 | `/community/waiting_list/` | `is_superuser` | 運営用アカウント設定「承認待集会一覧」 | — | 対象外（運営専用） |
| 集会 | 集会の承認 | `/community/accept/<id>/`（POST） | `is_superuser` | 承認待集会一覧 | — | 対象外（運営専用） |
| 集会 | 集会の非承認 | `/community/reject/<id>/`（POST） | `is_superuser` | 承認待集会一覧 | — | 対象外（運営専用） |
| 集会 | 集会の管理者クリーンアップ | `/community/<id>/admin-cleanup/`（POST） | `is_superuser` | 運営用の集会詳細操作 | — | 対象外（運営専用） |
| メンバー | メンバー管理 | `/community/<id>/members/` | 主催者のみ | 集会設定「メンバー管理」→「メンバーを管理」 | [スタッフを招待する](member/invite.md) | 記載あり |
| メンバー | スタッフ招待リンクの作成 | `/community/<id>/invite/create/`（POST） | 主催者のみ | メンバー管理「招待リンクを生成」 | [スタッフを招待する](member/invite.md) | 記載あり |
| メンバー | スタッフ招待リンクの削除 | `/community/<id>/invite/<招待ID>/revoke/`（POST） | 主催者のみ | メンバー管理「招待リンク」→「招待を取り消す…」→確認欄「招待を取り消す」 | [スタッフを招待する](member/invite.md) | 記載あり |
| メンバー | スタッフ招待の受け入れ | `/community/invite/<token>/` | リンクの所持者。確定時はログインが必要 | 共有されたリンク→「招待を受ける」 | [スタッフの招待を受ける](member/accept.md) | 記載あり |
| メンバー | メンバーの削除 | `/community/<id>/members/<メンバーID>/remove/`（POST） | 主催者のみ。自身・最後の主催者には制限 | メンバー一覧「メンバーを削除…」→確認欄「メンバーを削除する」 | [スタッフを削除する](member/remove.md) | 記載あり |
| メンバー | 主催者引き継ぎリンクの作成 | `/community/<id>/transfer/create/`（POST） | 現在の主催者のみ | 集会設定「危険な操作」→「主催者の引き継ぎ」→「引き継ぎリンクを作成」 | [主催者を引き継ぐ](member/transfer.md) | 記載あり |
| メンバー | 主催者引き継ぎの受け入れ | `/community/transfer/<token>/` | 有効なリンクの所持者。確定時はログインが必要 | 共有されたリンク→「主催者を引き継ぐ」 | [主催者を引き継ぐ](member/transfer.md) | 記載あり |
| メンバー | 主催者引き継ぎリンクの削除 | `/community/<id>/transfer/<招待ID>/revoke/`（POST） | 現在の主催者のみ | 集会設定「危険な操作」を開く→「リンクを削除」→確認欄「リンクを削除する」 | [主催者を引き継ぐ](member/transfer.md) | 記載あり |
| イベント | イベント管理 | `/event/my_list/` | ログインユーザー。表示する管理対象は所属集会 | アカウントメニューの集会名 | [イベント管理画面の見方](event/manage.md) | 記載あり |
| イベント | イベントの登録・開催周期の設定 | `/event/calendar/create/` | 承認済みで閉鎖されていない集会の主催者・スタッフ | イベント管理「イベント登録」→「開催日時」「繰り返し条件」→画面下「登録」 | [イベントを登録する](event/create.md) | 記載あり |
| イベント | 開始時刻の変更 | `/event/update/<id>/` | 対象集会の主催者・スタッフ、または `is_superuser`。過去回・Vket ロックに制限 | 開催回の「…」→「開始時刻を変更」→「開催日時」→画面下「保存」 | [開催日と開始時刻を変更する](event/edit.md) | 記載あり |
| イベント | 開催日の変更 | `/event/<id>/date/update/` | 対象集会の主催者・スタッフ、または `is_superuser`。過去回・Vket ロックに制限 | 開催回の「…」→「開催日を変更」→「開催日時」→画面下「変更する」 | [開催日と開始時刻を変更する](event/edit.md) | 記載あり |
| イベント | イベントの削除 | `/event/delete/<id>/`（通常は POST） | 対象集会の主催者のみ。Vket ロックに制限 | 開催回の「…」→「カレンダーから削除…」→カード内の確認欄。任意で「以降のイベントもまとめて削除する」→「カレンダーから削除する」 | [イベントを削除する](event/delete.md) | 記載あり |
| 発表 | 発表・特別企画・ブログの追加 | `/event/<イベントID>/detail/create/` | 対象集会の主催者・スタッフ、または `is_superuser` | イベント管理「発表を追加」→「内容」「日時」「資料」「記事」→画面下「保存」 | [発表情報を登録する](lt/create.md) | 記載あり |
| 発表 | 発表・特別企画・ブログの詳細 | `/event/detail/<id>/` | 承認済みは誰でも。未承認は対象集会の管理者・申請者本人・`is_superuser` | 発表一覧のカード／イベント管理「記事」 | [集会と発表を探す](browse/find.md)、[発表情報を登録する](lt/create.md) | 記載あり |
| 発表 | 発表情報の編集 | `/event/detail/<id>/update/` | 対象集会の主催者・スタッフ、`is_superuser`、本人の承認済み発表の申請者 | 発表詳細「編集」／自分の発表「編集」／イベント管理「編集」→各カードを編集→画面下「保存」 | [発表情報を編集・削除する](lt/edit-delete.md)、[申し込んだ発表を確認・更新する](speaker/manage.md) | 記載あり |
| 発表 | 発表情報の削除 | `/event/detail/<id>/delete/` | 対象集会の主催者・スタッフ、または `is_superuser`。Vket ロックに制限 | 発表の「…」→「この発表を削除…」→確認欄の「この発表を削除する」。専用の「発表の削除」画面では「削除する発表」のカードに開催日・テーマ・発表者を表示し、「キャンセル」で発表詳細へ戻る | [発表情報を編集・削除する](lt/edit-delete.md) | 記載あり |
| 発表 | 記事の生成 | `/event/generate_blog/<id>/`（POST） | 発表を編集できる人。発表タイプ・記事化 NG に制限 | 発表詳細「記事生成」／イベント管理の発表の「…」→「記事を生成」 | [ブログ記事を自動生成する](lt/auto-generate.md) | 記載あり |
| 発表 | 発表者との紐づけ用リンク作成 | `/event/detail/<id>/speaker-invite/`（POST） | 対象集会の主催者・スタッフ。承認済み発表で未紐づけ | 発表の「…」→「紐づけ用リンクを作成」→発表詳細の操作 | [発表とアカウントを紐づける](lt/link-speaker.md) | 記載あり |
| 発表 | 発表者アカウントの紐づけ確認 | `/event/speaker-link/` | 有効な招待リンクの所持者。確定時はログインが必要 | 主催者・スタッフから共有されたリンク→「紐づけ内容の確認」→「このアカウントに紐づける」。中止は「今は紐づけない」 | [発表とアカウントを紐づける](lt/link-speaker.md) | 記載あり |
| 発表 | 発表者アカウントの紐づけ解除 | `/event/detail/<id>/speaker-unlink/`（POST） | 対象集会の主催者・スタッフ | 発表詳細の紐づけ管理 | [発表とアカウントを紐づける](lt/link-speaker.md) | 記載あり |
| 発表 | 自分の発表 | `/event/detail/history/?mine=1` | ログインした発表者本人。承認済み発表 | アカウント設定「プロフィールと認証」→「自分の発表」→「発表を探す」で検索→カードの「編集」 | [発表とアカウントを紐づける](lt/link-speaker.md) | 記載あり |
| 発表 | 自分の発表の旧入口 | `/event/my_presentations/` | ログインユーザー | 本人絞り込みへ転送 | — | 対象外（独立画面なし） |
| 発表 | Markdown の書き方 | `/event/markdown/` | 誰でも | 発表編集画面「markdownの一覧と書き方」 | [発表情報を編集・削除する](lt/edit-delete.md) | 記載あり |
| 発表申請 | 集会への発表申し込み | `/event/apply/<集会ID>/` | ログインユーザー。対象開催日は受付中・当日以降 | 集会詳細の発表申請ボタン→「集会と開催回」「発表内容」「撮影・記事化」「追加情報」→画面下「申請する」 | [発表を申し込む](speaker/apply.md) | 記載あり |
| 発表申請 | 開催日を指定した発表申し込み | `/event/apply/<集会ID>/event/<イベントID>/` | ログインユーザー。対象開催日に限定 | 開催日を指定した申し込みリンク | [発表を申し込む](speaker/apply.md) | 記載あり |
| 発表申請 | 発表申請完了 | `/event/apply/<集会ID>/complete/` | ログインユーザー | 申請後。「発表申請完了: 集会名」→「申請一覧を見る」 | [発表を申し込む](speaker/apply.md) | 記載あり |
| 発表申請 | 自分の発表申請 | `/account/lt-applications/` | 申請者本人。対象となる Vket 由来発表の申込者も含む | イベント管理／自分の発表の「個人用」→「自分の発表申請」、申請完了「申請一覧を見る」 | [申し込んだ発表を確認・更新する](speaker/manage.md) | 記載あり |
| 発表申請 | 自分の発表の編集 | `/account/lt-applications/<id>/edit/` | 本人の発表。却下済みを除く。対象となる Vket 由来発表も含む | 「自分の発表申請」のカードの「編集」／結果・資料依頼メールのリンク→「内容」「資料」「撮影・記事化」→画面下「保存」 | [申し込んだ発表を確認・更新する](speaker/manage.md) | 記載あり |
| 発表申請 | 主催側の申請内容確認・日時調整 | `/event/application/<id>/review/` | 対象集会の主催者・スタッフ | イベント管理「申請内容」／新規申請の通知リンク→「申請内容」「追加情報」「審査の操作」 | [発表申請を承認・却下する](lt/approve.md) | 記載あり |
| 発表申請 | 発表申請の承認 | `/event/application/<id>/approve/`（POST） | 対象集会の主催者・スタッフ | イベント管理の「要対応」または承認待ち発表の「承認」→「承認する」 | [発表申請を承認・却下する](lt/approve.md) | 記載あり |
| 発表申請 | 発表申請の却下 | `/event/application/<id>/reject/`（POST） | 対象集会の主催者・スタッフ。却下理由必須 | イベント管理の「要対応」または承認待ち発表の「却下」→理由入力→「却下する」 | [発表申請を承認・却下する](lt/approve.md) | 記載あり |
| 発表申請 | 集会別申請一覧の旧入口 | `/community/<id>/applications/` | ログインユーザー | イベント管理へ転送。URL の集会 ID による画面表示はない | — | 対象外（旧 URL の転送のみ） |
| 外部連携 | VRCイベントカレンダー設定 | `/community/calendar_update/` | 選択中の集会の主催者・スタッフ | 集会設定「通知と連携」→「カレンダー設定」→「掲載文」「参加条件」「告知文」→画面下「更新」 | [VRCイベントカレンダーへの投稿を準備する](settings/event-calendar.md) | 記載あり |
| 外部連携 | Discord 通知の保存 | `/community/<id>/webhook/update/`（POST） | 対象集会の主催者・スタッフ | 集会設定「通知と連携」→「Discord通知」→画面下「通知を保存」 | [Discord Webhookを設定する](settings/discord.md) | 記載あり |
| 外部連携 | Discord 通知のテスト送信 | `/community/<id>/webhook/test/`（POST） | 対象集会の主催者・スタッフ。Webhook 設定済み | 集会設定「テスト送信」 | [Discord Webhookを設定する](settings/discord.md) | 記載あり |
| 外部連携 | 発表申請・撮影の設定保存 | `/community/<id>/lt-settings/update/`（POST） | 対象集会の主催者・スタッフ | 集会設定「発表の受付」→画面下「発表受付を保存」 | [発表申請の受付と撮影を設定する](settings/lt-application.md) | 記載あり |
| 外部連携 | API管理・APIキー一覧 | `/account/api-keys/` | ログインユーザー本人 | 集会設定「通知と連携」→「API管理」 | [APIキーを作成・無効化する](settings/api-key.md) | 記載あり |
| 外部連携 | APIキーの作成 | `/account/api-key/create/`（POST） | ログインユーザー本人 | API管理「新規APIキー作成」→「APIキーを作成」→画面上部の保管欄「コピー」（値を確認する時は「表示」） | [APIキーを作成・無効化する](settings/api-key.md) | 記載あり |
| 外部連携 | APIキーの無効化 | `/account/api-key/<id>/delete/`（POST） | キーの所有者本人 | API管理のキーのカード「無効化…」→確認欄「このAPIキーを無効化する」 | [APIキーを作成・無効化する](settings/api-key.md) | 記載あり |
| 告知・X | X告知テンプレート一覧 | `/twitter/template/list/` | 主催者・スタッフは所属集会分。`is_superuser` は全件 | 集会設定「通知と連携」→「テンプレート管理」 | [Xの告知文テンプレートを作る](settings/x-template.md) | 記載あり |
| 告知・X | X告知テンプレートの作成 | `/twitter/template/create/` | 対象集会の主催者・スタッフ、または `is_superuser` | テンプレート一覧「新規作成」／開催回の「X の告知文を作成」→画面下「テンプレートを保存」 | [Xの告知文テンプレートを作る](settings/x-template.md) | 記載あり |
| 告知・X | X告知テンプレートの編集 | `/twitter/template/<id>/update/` | 対象集会の主催者・スタッフ、または `is_superuser` | カード「編集」→画面下「テンプレートを保存」 | [Xの告知文テンプレートを作る](settings/x-template.md) | 記載あり |
| 告知・X | X告知テンプレートの削除 | `/twitter/template/<id>/delete/` | 対象集会の主催者・スタッフ、または `is_superuser` | カード「削除」→画面内の確認欄「テンプレートを削除する」 | [Xの告知文テンプレートを作る](settings/x-template.md) | 記載あり |
| 告知・X | X 告知のプレビュー | `/twitter/tweet/<イベントID>/<テンプレートID>/` | 対象集会の主催者・スタッフ、または `is_superuser` | 開催回の「X で告知」→テンプレート名／発表詳細のテンプレート名のボタン。「投稿文をコピー」「X を開いて予約する ↗」「X の投稿画面を開く ↗」 | [Xの告知文テンプレートを作る](settings/x-template.md) | 記載あり |
| 告知・X | ポストキュー一覧 | `/twitter/queue/` | 主催者・スタッフは所属集会分。`is_superuser` は全件 | ガイドの「ポストキューを開く」。運営用アカウント設定には「ポスト一覧」 | [Hub公式Xアカウントでの自動告知](event/auto-post.md) | 記載あり |
| 告知・X | ポストキュー詳細 | `/twitter/queue/<id>/` | 対象集会の主催者・スタッフは閲覧専用。`is_superuser` は編集可 | ポストキューのカード「詳細」 | [Hub公式Xアカウントでの自動告知](event/auto-post.md) | 記載あり |
| 告知・X | ポストキューの編集・再試行・即時投稿・削除 | `/twitter/queue/<id>/`（POST） | `is_superuser` のみ | ポストキュー詳細の操作 | — | 対象外（運営専用操作） |
| 告知・X | Discord 告知の予約一覧 | `/announcement/discord/` | `is_staff` または `is_superuser` | イベント管理「Discord 告知の予約」（「運営」表示） | — | 対象外（運営専用） |
| 告知・X | Discord 告知の新規予約 | `/announcement/discord/new/` | `is_staff` または `is_superuser` | 運営用の予約一覧「新しく予約する」 | — | 対象外（運営専用） |
| 告知・X | Discord 告知の詳細・予約編集 | `/announcement/discord/<id>/` | `is_staff` または `is_superuser` | 運営用の予約一覧 | — | 対象外（運営専用） |
| 告知・X | Discord 告知の予約取り消し | `/announcement/discord/<id>/cancel/` | `is_staff` または `is_superuser` | 運営用の予約詳細 | — | 対象外（運営専用） |
| 告知・X | Discord 告知の再送 | `/announcement/discord/<id>/resend/` | `is_staff` または `is_superuser` | 運営用の予約詳細 | — | 対象外（運営専用） |
| アクセス解析 | アクセス解析ダッシュボード | `/analytics/dashboard/` | ログインユーザー。データは所属集会分、`is_superuser` は全集会 | イベント管理「アクセス」／アカウント設定「プロフィールと認証」→「アクセス解析を見る」 | [アクセス解析を見る](analytics/dashboard.md) | 記載あり |
| アクセス解析 | 集計の CSV 出力 | `/analytics/dashboard/?format=csv` | ダッシュボードと同じ | ダッシュボード「CSV」 | [アクセス解析を見る](analytics/dashboard.md) | 記載あり |
| アクセス解析 | 検索キーワード一覧 | `/analytics/dashboard/search-queries/` | ログインユーザー。対象は閲覧可能な集会 | ダッシュボード「すべての検索キーワード（…語）→」 | [アクセス解析を見る](analytics/dashboard.md) | 記載あり |
| アクセス解析 | 配布リンクの管理 | `/analytics/campaigns/` | ログインユーザー。対象は閲覧可能な所属集会 | ダッシュボード上部「配布リンク」／本文「配布リンクを管理」 | [配布用のリンクとQRコードを作る](analytics/distribution-link.md) | 記載あり |
| アクセス解析 | 配布リンクの新規作成 | `/analytics/campaigns/new/` | 対象集会にアクセスできるログインユーザー | 配布リンクの管理「配布リンクを作成」→画面下「配布リンクを作成」 | [配布用のリンクとQRコードを作る](analytics/distribution-link.md) | 記載あり |
| アクセス解析 | 配布リンクの編集 | `/analytics/campaigns/<id>/edit/` | 対象集会にアクセスできるログインユーザー | カード「編集」→画面下「配布リンクを更新」 | [配布用のリンクとQRコードを作る](analytics/distribution-link.md) | 記載あり |
| アクセス解析 | 配布リンクの削除確認 | `/analytics/campaigns/<id>/delete/`（POST。GET は専用の確認画面） | 対象集会にアクセスできるログインユーザー | カード「…」→「削除…」→画面内の確認欄「配布リンクを削除する」 | [配布用のリンクとQRコードを作る](analytics/distribution-link.md) | 記載あり |
| アクセス解析 | 発表の反響表示 | `/event/detail/<id>/` 内 | 対象集会の管理者、または編集できる本人。本人と管理者で指標が異なる | 発表詳細内 | [アクセス解析を見る](analytics/dashboard.md) | 記載あり |
| Vket コラボ | コラボ一覧 | `/vket/` | 未ログインを含む閲覧者。運営以外は下書きを除外 | ガイド本文の「コラボ一覧」。共通ヘッダーには入口なし | [Vketコラボに参加登録する](vket/apply.md) | 記載あり |
| Vket コラボ | コラボ詳細・確定スケジュール | `/vket/<slug>/`（旧 `/vket/<id>/` は 301 で移動） | 未ログインを含む閲覧者。運営以外は下書きを除外 | コラボ一覧「詳細を見る」。確定分のスケジュールを表示 | [Vketコラボに参加登録する](vket/apply.md) | 記載あり |
| Vket コラボ | 最新コラボの参加状況への入口 | `/vket/status/` | ログインユーザー | 最新の対象コラボへ転送 | — | 対象外（独立画面なし） |
| Vket コラボ | 参加状況 | `/vket/<id>/status/` | ログインユーザー。参加情報は選択中の所属集会分。未ログイン時は案内のみ | イベント管理のコラボバナー等 | [Vketコラボに参加登録する](vket/apply.md) | 記載あり |
| Vket コラボ | 参加登録・日程／発表情報の編集 | `/vket/<id>/apply/` | 所属集会の主催者・スタッフ。締切・フェーズ・申込状態で編集範囲が変わる | コラボ詳細「参加登録」／参加状況「参加登録する」「編集」／リンク列「参加登録・変更」→各カードを入力→保存バー「保存する」 | [Vketコラボに参加登録する](vket/apply.md) | 記載あり |
| Vket コラボ | ステージ登録用の入力値・サムネイル | `/vket/<id>/stage-copy/<参加ID>/` | 対象集会の主催者・スタッフ、または運営 | 参加状況「コピー用ページを開く」 | [Vketステージへの登録を進める](vket/stage.md) | 記載あり |
| Vket コラボ | ステージ用サムネイル生成 | `/vket/<id>/stage-copy/<参加ID>/thumbnail/`（POST） | 上記と同じ。ステージ登録の受付開始後 | 「サムネイルを1回生成する」 | [Vketステージへの登録を進める](vket/stage.md) | 記載あり |
| Vket コラボ | ステージ用サムネイル取得 | `/vket/<id>/stage-copy/<参加ID>/thumbnail/download/` | 対象集会の主催者・スタッフ、または運営。生成済み画像が必要 | 「JPEGをダウンロード」 | [Vketステージへの登録を進める](vket/stage.md) | 記載あり |
| Vket コラボ | ステージ登録完了の記録 | `/vket/<id>/stage-register/` | 対象集会メンバー。参加・受付・開催期間等に制限。GET は案内 | 参加状況「登録したことを記録する」→カード内の確認欄「登録完了を記録する」 | [Vketステージへの登録を進める](vket/stage.md) | 記載あり |
| Vket コラボ | コラボのお知らせ一覧 | `/vket/<id>/notices/` | ログインユーザー。受信一覧は所属集会分。未ログイン時は案内 | 参加状況の「確認する」「すべて見る」／リンク列「お知らせ」 | [コラボのお知らせを確認する](vket/notices.md) | 記載あり |
| Vket コラボ | お知らせを確認済みにする | `/vket/<id>/notices/<受信ID>/ack/`（POST） | 対象集会のメンバー | お知らせ一覧の確認操作 | [コラボのお知らせを確認する](vket/notices.md) | 記載あり |
| Vket コラボ | 共有リンクのお知らせ確認 | `/vket/ack/<確認token>/` | 有効なリンクの所持者。ログイン不要 | 共有された確認リンク | [コラボのお知らせを確認する](vket/notices.md) | 記載あり |
| Vket コラボ | 参加登録した発表の削除 | `/vket/<id>/presentations/<発表ID>/delete/`（POST） | 選択中の対象集会メンバー。発表編集の受付期間内 | 参加状況の発表の「削除」→同じ発表の確認欄「この発表を削除する」 | [Vketコラボに参加登録する](vket/apply.md) | 記載あり |
| Vket コラボ | コラボ運営管理 | `/vket/<id>/manage/` | `is_staff` または `is_superuser` | 運営用「管理」「管理画面を開く」 | — | 対象外（運営専用） |
| Vket コラボ | 運営用の日程管理 | `/vket/<id>/manage/schedule/` | `is_staff` または `is_superuser` | 運営管理画面 | — | 対象外（運営専用） |
| Vket コラボ | 運営用の日程設定保存 | `/vket/<id>/manage/schedule/settings/` | `is_staff` または `is_superuser` | 運営用の日程管理 | — | 対象外（運営専用） |
| Vket コラボ | 運営用のお知らせ管理 | `/vket/<id>/manage/notices/` | `is_staff` または `is_superuser` | 運営管理画面 | — | 対象外（運営専用） |
| Vket コラボ | 運営用のお知らせ作成 | `/vket/<id>/manage/notices/create/` | `is_staff` または `is_superuser` | 運営用のお知らせ管理 | — | 対象外（運営専用） |
| Vket コラボ | 運営用のお知らせ編集 | `/vket/<id>/manage/notices/<お知らせID>/edit/` | `is_staff` または `is_superuser` | 運営用のお知らせ管理 | — | 対象外（運営専用） |
| Vket コラボ | コラボ情報の公開 | `/vket/<id>/manage/publish/` | `is_staff` または `is_superuser` | 運営管理画面 | — | 対象外（運営専用） |
| Vket コラボ | 運営用の参加情報更新 | `/vket/<id>/manage/participations/<参加ID>/update/` | `is_staff` または `is_superuser` | 運営管理画面 | — | 対象外（運営専用） |
| Vket コラボ | 運営用の発表削除 | `/vket/<id>/manage/presentations/<発表ID>/delete/` | `is_staff` または `is_superuser` | 運営管理画面 | — | 対象外（運営専用） |
| 閲覧（カレンダー・一覧・ニュース） | ホーム・開催予定・カレンダー連携 | `/` | 誰でも | ヘッダー「ホーム」／サイト名 | [集会と発表を探す](browse/find.md)、[予定を自分のカレンダーに追加する](browse/calendar.md) | 記載あり |
| 閲覧（カレンダー・一覧・ニュース） | 開催日程 | `/event/list/` | 誰でも | フッター「開催日程」／ホーム「すべてのイベントを見る」 | [集会と発表を探す](browse/find.md) | 記載あり |
| 閲覧（カレンダー・一覧・ニュース） | 発表一覧・検索 | `/event/detail/history/` | 誰でも | ヘッダー／フッター「発表一覧」 | [集会と発表を探す](browse/find.md) | 記載あり |
| 閲覧（カレンダー・一覧・ニュース） | 資料なしを含む発表一覧 | `/event/detail/history/?view=all` | 誰でも | 発表一覧の表示切り替え | [集会と発表を探す](browse/find.md) | 記載あり |
| 閲覧（カレンダー・一覧・ニュース） | 特別企画・ブログ一覧 | `/event/detail/history/?type=special` | 誰でも | 発表一覧の種別切り替え | [集会と発表を探す](browse/find.md) | 記載あり |
| 閲覧（カレンダー・一覧・ニュース） | 特別企画・ブログの旧入口 | `/event/event_log/` | 誰でも | 新しい一覧へ転送 | — | 対象外（互換転送） |
| 閲覧（カレンダー・一覧・ニュース） | お知らせ一覧 | `/news/` | 誰でも。運営以外は公開記事のみ | ヘッダー／フッター「お知らせ」 | [集会と発表を探す](browse/find.md) | 記載あり |
| 閲覧（カレンダー・一覧・ニュース） | お知らせのカテゴリ一覧 | `/news/category/<カテゴリ>/` | 誰でも。運営以外は公開記事のみ | お知らせのカテゴリ選択／フッターのカテゴリリンク | [集会と発表を探す](browse/find.md) | 記載あり |
| 閲覧（カレンダー・一覧・ニュース） | 活動記録カテゴリの旧入口 | `/news/category/record/` | 誰でも | `/news/category/activity/` へ転送 | — | 対象外（互換転送） |
| 閲覧（カレンダー・一覧・ニュース） | お知らせ詳細 | `/news/<slug>/` | 誰でも。運営以外は公開記事のみ | お知らせ一覧の記事 | [集会と発表を探す](browse/find.md) | 記載あり |
| 閲覧（カレンダー・一覧・ニュース） | お知らせの作成 | `/news/create/` | ログイン済みで `is_staff` | 運営用の作成操作 | — | 対象外（運営専用） |
| 閲覧（カレンダー・一覧・ニュース） | お知らせの編集 | `/news/<slug>/edit/` | ログイン済みで `is_staff` | 運営用の記事編集操作 | — | 対象外（運営専用） |
| 閲覧（カレンダー・一覧・ニュース） | お知らせの削除 | `/news/<slug>/delete/` | ログイン済みで `is_staff` | 運営用の記事削除操作 | — | 対象外（運営専用） |
| 閲覧（カレンダー・一覧・ニュース） | 運営について | `/about/` | 誰でも | フッター「運営について」 | [集会と発表を探す](browse/find.md) | 記載あり |
| 閲覧（カレンダー・一覧・ニュース） | プライバシーポリシー | `/privacy/` | 誰でも | フッター「プライバシーポリシー」／新規登録 | [集会と発表を探す](browse/find.md) | 記載あり |
| 閲覧（カレンダー・一覧・ニュース） | 利用規約 | `/terms/` | 誰でも | フッター「利用規約」／新規登録 | [集会と発表を探す](browse/find.md) | 記載あり |
| ガイド | 使い方ガイドの目次 | `/guide/` | 誰でも閲覧可能。共通メニューのリンクはログイン時のみ | ヘッダー／フッター「使い方」 | [はじめに](index.md) | 記載あり |
| ガイド | ガイド本文 | `/guide/<path>/` | 誰でも | ガイド目次・前後ページのリンク | [はじめに](index.md) | 記載あり |
| ガイド | 自動告知ガイドの旧入口 | `/guide/event/auto-tweet/` | 誰でも | `/guide/event/auto-post/` へ転送 | — | 対象外（互換転送） |
| ガイド | ガイド内部資料 | `/guide/_…/`、`/guide/AGENTS/` 等 | 公開不可 | メニューなし。404 | — | 対象外（内部資料） |
| 内部処理 | カレンダー同期 | `/event/sync/` | リクエスト用トークンで認証 | 通常画面の入口なし | — | 対象外（自動処理） |
| 内部処理 | 定期イベント生成 | `/event/generate/` | リクエスト用トークンで認証 | 通常画面の入口なし | — | 対象外（自動処理） |
| 内部処理 | 記事生成待ちの処理 | `/event/article-generation/run/` | リクエスト用トークンで認証 | 通常画面の入口なし | — | 対象外（自動処理） |
| 内部処理 | 資料登録依頼の送信 | `/event/send-material-upload-reminders/` | リクエスト用トークンで認証 | 通常画面の入口なし | — | 対象外（自動処理） |
| 内部処理 | スライド公開リマインドの送信 | `/event/send-slide-reminders/` | リクエスト用トークンで認証 | 通常画面の入口なし | — | 対象外（自動処理） |
| 内部処理 | 発表紐づけトークンの受け渡し | `/event/speaker-link/token/`（POST） | 招待トークンを検証 | 紐づけ画面の JavaScript が使用 | — | 対象外（画面内部の処理） |
| 内部処理 | 予約ポストの送信 | `/twitter/post-scheduled/` | リクエスト用トークンで認証 | 通常画面の入口なし | — | 対象外（自動処理） |
| 内部処理 | 予約 Discord 告知の送信 | `/announcement/discord/send-scheduled/` | リクエスト用トークンで認証 | 通常画面の入口なし | — | 対象外（自動処理） |
| 内部処理 | アクセス解析同期 | `/analytics/sync/` | リクエスト用トークンで認証 | 通常画面の入口なし | — | 対象外（自動処理） |
| 内部処理 | コラボ日程の自動確定 | `/vket/auto-confirm/run/` | リクエスト用トークンで認証 | 通常画面の入口なし | — | 対象外（自動処理） |
| API | API の入口 | `/api/v1/` | API クライアント | 通常メニューなし | — | 対象外（機械向け API） |
| API | 集会一覧・詳細の取得 | `/api/v1/community/`、`/api/v1/community/<id>/` | 公開データを読む API クライアント | 通常メニューなし | — | 対象外（機械向け API） |
| API | ワールド表示用の集会一覧取得 | `/api/v1/community/gathering-list/` | 公開データを読む API クライアント | ワールド内アセットから利用 | — | 対象外（機械向け API） |
| API | イベント一覧・詳細の取得 | `/api/v1/event/`、`/api/v1/event/<id>/` | 公開データを読む API クライアント | 通常メニューなし | — | 対象外（機械向け API） |
| API | 公開発表一覧・詳細の取得 | `/api/v1/event_detail/`、`/api/v1/event_detail/<id>/` | 公開データを読む API クライアント | 通常メニューなし | — | 対象外（機械向け API） |
| API | 発表情報の取得・作成・更新・削除 | `/api/v1/event-details/`、`/api/v1/event-details/<id>/` | API キー／セッション認証。対象集会・操作別の制限あり | API管理の説明 | — | 対象外（機械向け API） |
| API | 所属集会の発表情報取得 | `/api/v1/event-details/my_events/` | 認証済み API クライアント | 通常メニューなし | — | 対象外（機械向け API） |
| API | 発表申請のレビュー | `/api/v1/event-details/<id>/review/` | API キー認証・`is_superuser` | 通常メニューなし | — | 対象外（運営用 API） |
| API | Discord 情報からの集会取得 | `/api/v1/community-by-discord/` | 認証済み・`is_superuser` | 通常メニューなし | — | 対象外（運営用 API） |
| API | 開催周期のプレビュー | `/api/v1/recurrence-preview/` | 認証済みユーザー | フォーム等から利用 | — | 対象外（画面内部の API） |
| API | 定期ルール一覧・詳細の取得 | `/api/v1/recurrence-rules/`、`/api/v1/recurrence-rules/<id>/` | 認証必須。取得対象が返るのは `is_superuser` | 通常メニューなし | — | 対象外（運営用 API） |
| API | 定期ルールの未来回削除 | `/api/v1/recurrence-rules/<id>/delete_future_events/` | 認証必須。対象取得は `is_superuser` のみ | 通常メニューなし | — | 対象外（運営用 API） |
| API | API 用ログイン入口 | `/api-auth/login/` | 未ログインの利用者 | API 閲覧用の入口。同じ公開ログイン view を使用 | — | 対象外（既存ログイン画面の別入口） |
| API | API 用ログアウト | `/api-auth/logout/` | API 閲覧用セッションの利用者 | API 閲覧用の認証操作 | — | 対象外（API 閲覧用補助画面） |
| API | API スキーマ | `/api/schema/` | API 利用者 | 通常メニューなし | — | 対象外（開発者向け仕様データ） |
| API | API ドキュメント画面 | `/api/docs/` | 開発時のみ | URL 直接 | — | 対象外（DEBUG 時限定） |
| API | API 仕様閲覧画面 | `/api/redoc/` | 開発時のみ | URL 直接 | — | 対象外（DEBUG 時限定） |
| 管理 | 管理画面 | `/admin/` 以下 | 管理画面のアクセス権を持つ運営 | 運営用の管理画面リンク | — | 対象外（admin） |
| 管理 | 管理画面のログイン入口 | `/admin/login/` | — | 公開ログインへ転送 | — | 対象外（独立画面なし） |
| 内部配信 | ヘルスチェック | `/health` | 監視処理 | 通常メニューなし | — | 対象外（監視用応答） |
| 内部配信 | 発表申請の共有画像 | `/og/lt-apply/<id>.png` | リンクプレビュー取得処理 | 自動取得 | — | 対象外（画像配信） |
| 内部配信 | 集会の共有画像 | `/og/community/<id>.png` | リンクプレビュー取得処理 | 自動取得 | — | 対象外（画像配信） |
| 内部配信 | ガイドの共有画像 | `/og/guide/<path>.png` | リンクプレビュー取得処理 | 自動取得 | — | 対象外（画像配信） |
| 内部配信 | コラボお知らせ一覧の共有画像 | `/vket/<id>/notices/og.png` | リンクプレビュー取得処理 | 自動取得 | — | 対象外（画像配信） |
| 内部配信 | コラボお知らせ個別の共有画像 | `/vket/<id>/notices/<お知らせID>/og.png` | 署名等を検証する画像取得処理 | 自動取得 | — | 対象外（画像配信） |
| 内部配信 | 機械向けサイト案内 | `/llms.txt` | 機械クライアント | 通常メニューなし | — | 対象外（機械向けテキスト） |
| 内部配信 | ホームの Markdown 表現 | `/index.md` | 機械クライアント等 | 通常メニューなし | — | 対象外（機械向け表現） |
| 内部配信 | サイトマニフェスト | `/manifest.json` | ブラウザ | 自動取得 | — | 対象外（ブラウザ用データ） |
| 内部配信 | サイトアイコン | `/favicon.ico` | ブラウザ | 自動取得 | — | 対象外（画像配信） |
| 内部配信 | ホーム画面用アイコン | `/apple-touch-icon.png` | ブラウザ | 自動取得 | — | 対象外（画像配信） |
| 内部配信 | ホーム画面用アイコンの別入口 | `/apple-touch-icon-precomposed.png` | ブラウザ | 自動取得 | — | 対象外（画像配信） |
| 内部配信 | サイズ指定アイコン | `/apple-touch-icon-152x152.png` | ブラウザ | 自動取得 | — | 対象外（画像配信） |
| 内部配信 | サイズ指定アイコンの別入口 | `/apple-touch-icon-152x152-precomposed.png` | ブラウザ | 自動取得 | — | 対象外（画像配信） |
| 内部配信 | 静的ファイル | `STATIC_URL` 以下（開発用 URL 登録） | ブラウザ | ページから自動取得 | — | 対象外（ファイル配信） |
| 内部配信 | 検索エンジン向け案内 | `/robots.txt` | 検索クローラー | 自動取得 | — | 対象外（機械向けデータ） |
| 内部配信 | サイトマップ | `/sitemap.xml` | 検索クローラー | 自動取得 | — | 対象外（機械向けデータ） |
| 内部配信 | サイトマップの別入口 | `/sitemaps.xml` | 検索クローラー | `/sitemap.xml` へ転送 | — | 対象外（互換転送） |
