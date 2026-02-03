# Status

## 最終更新
2026-02-03 13:45

## 現在のフォーカス
なし（タスク完了）

## 進行中タスク
なし

## 直近の完了タスク
- [x] community/update 500エラー修正 (2026-02-03)
  - コード修正デプロイ完了
  - R2ストレージバックアップ取得（1.2GB）
  - 本番DB修正: 47件のposter_imageパスを正しいパスに更新
  - 全ての画像URLが正常にアクセス可能

## コンテキスト（重要な前提）
- poster_imageパス破損の原因: resize_and_convert_imageが毎回呼ばれ、パスに`poster/`が追加され続けた
- 修正: `_committed`チェックで新規アップロード時のみリサイズ
- R2バックアップ: `/Users/ms25/project/vrc-ta-hub/backup/r2-storage-20260203/`

## 次にやること
なし（P0タスク完了）

## ブロッカー・未解決
なし

## 学んだこと（セッション中に発見）
- Django save() の update_fields はオーバーライドされた save() 内の処理には影響しない [昇格済み]
- ImageField.save() でファイル名にディレクトリパスが含まれていると upload_to と二重になる [昇格済み]
- resize_and_convert_image が毎回呼ばれると、save のたびにパスに poster/ が追加されてネストする
- _committed 属性で新規アップロードかどうかを判定できる（False = 未保存）
- R2ストレージでは壊れたパス（poster/poster/...）にもファイルが実際に保存されていた
- AWS CLIでR2に接続する際は `--region auto` を指定
