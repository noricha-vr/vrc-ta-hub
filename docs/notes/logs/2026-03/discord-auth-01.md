## Discord再連携で競合アカウントを自己回復できるようにした
- 日付: 2026-03-24
- 関連: #108
- 状況: 既存ユーザーが設定画面から Discord 連携をやり直したとき、同じ Discord UID が過去の重複アカウントに残っているケースを修正していた。
- 問題: `lookup()` 後に `sociallogin.is_existing=True` になるため、`process=connect` を allauth デフォルトに任せるだけでは競合アカウントが残り、コミュニティ所属も分断されたままだった。
- 対応: `pre_social_login()` で `process=connect` を先に判定し、競合 `SocialAccount` の再割り当てと `CommunityMember` の移動を実施するテストを追加した。
- → how/discord-auth.md に知識として追記済み
