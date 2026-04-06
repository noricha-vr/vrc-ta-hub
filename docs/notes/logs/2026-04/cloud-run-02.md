## Cloud Run preview host を nginx 前段でも正規化した
- 日付: 2026-04-06
- 関連: #193
- 状況: `rev-24d1224---vrc-ta-hub-...a.run.app` へのアクセスで `DisallowedHost` が再度観測され、Django middleware 側の補正だけでは前段の raw host 通過を完全には潰し切れていなかった。
- 問題: nginx が `proxy_set_header Host $http_host;` のままで、Cloud Run preview host をそのまま Django に渡していた。
- 対応:
  1. `nginx-app.conf` に `vrc-ta-hub` 向け preview host だけを `vrc-ta-hub.com` に写像する `map` を追加
  2. upstream に渡す `Host` を `$django_upstream_host` に切り替えた
  3. `website.tests.test_nginx_config` を追加し、nginx 側の host 正規化ルールを回帰テスト化した
- 関連PR: https://github.com/noricha-vr/vrc-ta-hub/pull/193
