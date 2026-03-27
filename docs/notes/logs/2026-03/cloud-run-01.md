## Cloud Run のリビジョンURLで `DisallowedHost` が発生
- 日付: 2026-03-27
- 関連: #129
- 状況: `vrc-ta-hub` の Cloud Run リビジョンURL `rev-24d1224---vrc-ta-hub-mhbhtr6sha-an.a.run.app` でアクセス時に 500 エラーが発生していた。
- 問題: `app/website/settings.py` の `ALLOWED_HOSTS` が独自ドメイン・localhost・`HTTP_HOST` 環境変数しか見ておらず、Cloud Run のタグ付きリビジョンURLを許可していなかった。
- 対応: `ALLOWED_HOSTS` の組み立てを関数化し、`.a.run.app` と `ALLOWED_HOSTS` 環境変数を扱えるように修正した。Cloud Run リビジョンURLを再現するテストも追加した。
- → how/cloud-run.md に知識として追記済み
