# mypy + django-stubs 段階導入ガイド

このプロジェクトでは静的型チェックを **warn-only (警告のみ、CI で失敗させない)** で段階的に導入する。
最初は `app/event/services/` だけを対象にし、エラー件数を観測しながら範囲を広げる。

## 段階導入方針

| Phase | 対象 | ゴール |
|-------|------|--------|
| Phase 1 (現在) | `app/event/services/` | warn-only を維持し、エラー 0 を継続 |
| Phase 2 | `app/event/recurrence/` | warn-only |
| Phase 3 | `app/twitter/generators/` | warn-only |
| Phase 4 | Phase 1〜3 を strict 化 | warn 0 達成 → `disallow_untyped_defs = true` |

**既存コードへの型ヒント大量追加は本 PR では行わない**。
各サービスを触る PR で「触る関数だけ型を付ける」運用にして、自然に型が育つようにする。

## ローカルでの実行方法

### 補助スクリプト (推奨)

```bash
bash scripts/run_mypy.sh
```

`pyproject.toml` の `[tool.mypy]` 設定を参照し、`app/event/services/` を warn-only で
チェックし、最後にエラー件数を表示する。

### Docker コンテナ内で実行

`requirements.txt` に mypy + django-stubs を入れているので、コンテナ再ビルド後は
使い捨てコンテナ内でも実行できる。ワークツリー全体を読み取り専用でマウントするため、
起動中コンテナの状態には依存しない。

```bash
docker compose build vrc-ta-hub
docker compose run --rm --no-deps \
  -v "$PWD:/workspace:ro" -w /workspace \
  vrc-ta-hub mypy app/event/services/ --config-file pyproject.toml
```

依存同期済みのローカル環境では、同じ検証を次のコマンドで実行できる。

```bash
mypy app/event/services/ --config-file pyproject.toml
```

## 設定の見どころ (`pyproject.toml`)

```toml
[tool.mypy]
python_version = "3.12"
mypy_path = "app"                               # app/ を起点にモジュール解決する
plugins = ["mypy_django_plugin.main", "mypy_drf_plugin.main"]
strict_optional = true                          # None 安全性は最初から有効
warn_unused_configs = true
ignore_missing_imports = true                   # 第三者ライブラリの型欠如で爆発させない
follow_imports = "silent"                       # フォロー先 import の警告は抑制

[[tool.mypy.overrides]]
module = "event.services.*"
disallow_untyped_defs = false                   # 段階導入: 未注釈関数を許容
warn_return_any = true                          # Any を返す関数だけは警告する

[tool.django-stubs]
django_settings_module = "website.settings"
```

`follow_imports = "silent"` と `ignore_missing_imports = true` がポイント。
これがないと、対象外モジュール側のエラーまで吸い込んでノイズが激増する。

## 解消済みの既知エラー

Phase 1 の初回実行で確認した 5 件は解消済み。CI の warn-only 設計は維持しつつ、
`app/event/services/` の mypy はエラー 0 を基準とする。

### 1. `Library stubs not installed` (bleach / markdown)

```
event/services/markdown_processor.py:13: error: Library stubs not installed for "bleach"  [import-untyped]
event/services/markdown_processor.py:14: error: Library stubs not installed for "markdown"  [import-untyped]
event/services/markdown_processor.py:15: error: Library stubs not installed for "bleach.css_sanitizer"  [import-untyped]
```

**採用した対処**: `types-bleach` / `types-Markdown` を固定バージョンで導入した。
型エラーの抑制コメントは追加せず、第三者ライブラリの公開スタブで解決している。

### 2. `Returning Any from function declared to return "str"`

```
event/services/markdown_processor.py:222: error: Returning Any from function declared to return "str"  [no-any-return]
```

**採用した対処**: 型スタブを導入し、`markdown.markdown()` の戻り値を `str` として
明示した。サニタイズ後の HTML もスタブに基づき `str` として検証される。

### 3. OpenAI Completions.create の overload 不一致

```
event/services/content_generation_service.py:244: error: No overload variant of "create" of "Completions" matches argument types ...
```

**採用した対処**: `messages`、`tools`、`tool_choice` を OpenAI SDK の正式型
(`ChatCompletionMessageParam` など) で明示した。リクエスト内容は変えず、
型エラーの抑制コメントも使用していない。

### Django ORM の型不一致 (将来出てくるパターン)

`User.objects.filter(...).first()` の戻り値が `Model | None` になるため、
そのまま属性アクセスすると `Item "None" of "Model | None" has no attribute ...` が出る。

**対処**: 早期 return で None を弾く。

```python
user = User.objects.filter(id=user_id).first()
if user is None:
    return
# ここから user は User として扱える
```

## CI 連携

`.github/workflows/ci.yml` の mypy job は `continue-on-error: true` の warn-only を維持する。
`app/event/services/` のエラー 0 は観測対象の基準であり、CI の blocking 化は Phase 5 まで行わない。

## 将来計画

1. **Phase 1 (現在)**: `event/services/` の warn-only 観測を継続し、エラー 0 を維持
2. **Phase 2**: `event/recurrence/` に観測範囲を拡大
3. **Phase 3**: `twitter/generators/` に観測範囲を拡大
4. **Phase 4**: Phase 1〜3 で `disallow_untyped_defs = true` に昇格
5. **Phase 5**: `strict = true` 相当に到達したアプリから、CI job を warn-only から
   blocking に切り替える

各 Phase は独立した PR で進める。一気に厳密化しない。
