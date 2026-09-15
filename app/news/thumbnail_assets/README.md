# news サムネイル生成用アセット

`scripts/generate_news_thumbnails.py` が使うフォント。static 配下には置かない（collectstatic で R2 に公開されるのを避けるため）。

## fonts/NotoSansJP-Bold.otf

- 出典: https://github.com/notofonts/noto-cjk `Sans/SubsetOTF/JP/NotoSansJP-Bold.otf`（日本語サブセット版）
- 取得日: 2026-09-15（noto-cjk main `f8d157532fbfaeda587e826d4cd5b21a49186f7c`）
- SHA256: `1b0edfb500b73a4fa8a4fcaae1bbbd403994e08e73e3e0da37e70d3853f42c5f`
- ライセンス: SIL Open Font License 1.1（`fonts/OFL.txt`）。Copyright the Noto Project Authors

再取得:

```bash
curl -sSL -o app/news/thumbnail_assets/fonts/NotoSansJP-Bold.otf \
  https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/SubsetOTF/JP/NotoSansJP-Bold.otf
```

## 画像の再生成ルール

- 出力先は `app/news/static/news/images/og/`、サイズは 1200×630 固定（`detail.html` の og:image:width/height と対応）
- static は manifest ハッシュ無しで CDN キャッシュされるため、作り直す時はファイル名の `-vN` を上げて `news/thumbnail_specs.py` のマップも更新する
- DB で記事タイトルを改題しても画像は変わらない。`thumbnail_specs.py` のタイトルを直し `-v2` で再生成する
