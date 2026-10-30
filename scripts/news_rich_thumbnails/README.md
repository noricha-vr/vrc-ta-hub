# お知らせのリッチ版サムネイル

左に見出し、右に記事の内容が分かる画像を置く 1200×630 の画像を作る。制作時のスクリプトで、配信は `app/news/thumbnail_specs.py` の定義を使う。素材・作業用 PNG・`check.json` はリポジトリへ入れない。

## 素材を集める

Python と Pillow、撮影には Playwright と Chromium が必要。描画は macOS のヒラギノ角ゴシック W4 / W6 / W7 / W8 を使う。素材用ディレクトリと出力用ディレクトリは任意の場所に作れる。以下はリポジトリのルートで実行する。

```bash
python scripts/news_rich_thumbnails/capture.py --out /tmp/news-rich/src
```

`capture.py` は発表一覧のカード（`history_cards.png`）、トップ（`top_view.png`・`top_hero.png`）、カレンダー追加の帯（`cal_banner.png`）、カレンダー（`cal_iframe.png`）、フッター（`footer.png`）を撮る。帯のボタンの位置は `capture_meta.json` に記録する。

残りの素材は同じ素材ディレクトリへ手で置く。

| 素材ファイル | 集め方 |
|---|---|
| `misskey.png` | 告知ノートの本文・タグ・画像・リンクカードが見える画面を撮影する |
| `rec_0.jpg` 〜 `rec_3.jpg` | 自動撮影の記事で紹介している発表動画の YouTube サムネイル |
| `v8_0.jpg`・`v8_1.jpg`・`v8_3.jpg`・`v8_4.jpg` | Vket 2025 Summer の発表動画の YouTube サムネイル |
| `v9_0.jpg`・`v9_1.jpg`・`v9_2.jpg`・`v9_4.jpg` | Vket 2025 Winter の発表動画の YouTube サムネイル |
| `v10_0.jpg`・`v10_1.jpg`・`v10_3.jpg`・`v10_4.jpg` | Vket 2026 Summer の発表動画の YouTube サムネイル |
| `book.jpg`・`nyumon_cover-01.png` | 対象の書影・表紙を手で置く |
| `asset.jpg`・`vket25s.jpg` | アセットの紹介画像・Vket 2025 Summer の告知ビジュアルを手で置く |

YouTube は記事の動画 ID を確認して `https://i.ytimg.com/vi/<id>/maxresdefault.jpg` から取得し、上記の名前で保存する。見せる位置の調整は素材側の切り抜きか `generate.py` の `SPECS` で行う。

## 生成して点検する

```bash
python scripts/news_rich_thumbnails/generate.py --src /tmp/news-rich/src --out /tmp/news-rich/out
```

出力名は `<slug>.png`。`check.json` に、見出し 56px 以上（`headline_ok`）、見出しと画像が重ならない（`no_overlap`）、画像がはみ出ない（`panel_inside`）、右側パネルに対する素材画像の面積が 3 割以上（`panel_ok`）の自動点検結果が出る。各項目が真であることを確認する。

さらに 1 枚ずつ目で「見せたいところが見えているか」「見出しと合っているか」を確かめる。一覧カード相当の幅と 16:9 の切り抜きでも、文字が読め、表紙や画面の大事な部分が隠れていないかを見る。自動点検だけで完成としない。

## static へ置く

点検した PNG を JPEG（品質 86）に変換する。既存 URL のキャッシュを壊さないよう `-vN` を上げ、`app/news/static/news/images/og/<slug>-vN.jpg` に置く。例:

```bash
python - <<'PY'
from pathlib import Path
from PIL import Image

slug = 'website-management'
version = 2  # 作り直すたびに上げる
with Image.open(Path('/tmp/news-rich/out') / f'{slug}.png') as image:
    assert image.size == (1200, 630)
    image.convert('RGB').save(
        Path('app/news/static/news/images/og') / f'{slug}-v{version}.jpg',
        format='JPEG', quality=86, optimize=True,
    )
PY
```

`RICH_POST_THUMBNAIL_SPECS` のファイル名を更新する。従来の文字画像とカテゴリ既定画像は削除しない。アップロードより static を優先する必要がある記事だけ `prefer_over_upload=True` を付ける。文字だけの生成器の `POST_THUMBNAIL_SPECS` にはリッチ版を入れない。
