# Discord OAuth 競合解消パターン

## `process=connect` で古い連携が残っている場合の回復
- 問題: 設定画面から Discord 連携をやり直すとき、同じ Discord UID が過去の重複ユーザーに残っていると `lookup()` 後に `is_existing=True` となり、通常の connect フローだけでは競合を解消できない。
- 解決: `pre_social_login()` で `process=connect` を `is_existing` より先に判定し、競合 `SocialAccount` を現在ユーザーへ再割り当てしてから allauth の connect フローを継続させる。
- 教訓: allauth の `process=connect` は「既存 SocialAccount があるか」だけで早期 return しやすいので、設定画面からの再連携で整合性回復をさせたい場合は `lookup()` 後の状態変化まで考慮する。

## コミュニティ所属の引き継ぎ
- 問題: Discord 連携だけを現在ユーザーへ戻しても、旧ユーザー側に `CommunityMember` が残ると所属情報が分断されたままになる。
- 解決: 競合元ユーザーの `CommunityMember` を走査し、現在ユーザーが未所属のコミュニティだけ移動する。
- 教訓: アカウント統合系の修正では認証情報だけでなく、周辺の所属・権限データも一緒に整合させる。

## owner アカウントと staff 本人アカウントを混同しない
- 問題: staff が複数集会の運用を担当しているケースでは、Discord が一時的に集会 owner アカウントへ接続されていても、既存 owner/staff アカウントを安易にマージすると別人の権限や連絡先を壊す。
- 解決: Discord の `SocialAccount` とメールアドレスが本人一致するアカウントを本体候補として扱い、既存の owner/staff に別 Discord や別メールがある場合はマージせず、本人アカウントへ `CommunityMember(role=staff)` を追加する。
- 教訓: 「同じ集会に関わっている」だけでは統合根拠にならない。Discord UID・メールアドレス・既存 role を合わせて確認し、別人の owner/staff はそのまま残す。
