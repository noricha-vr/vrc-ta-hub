# mypy + django-stubs 段階導入ガイド

このプロジェクトでは静的型チェックを **warn-only (警告のみ、CI で失敗させない)** で段階的に導入する。
最初は `app/event/services/` だけを対象にし、エラー件数を観測しながら範囲を広げる。

## 段階導入方針

| Phase | 対象 | ゴール |
|-------|------|--------|
| Phase 1 (現在) | `app/event/services/` | warn-only でエラー件数を可視化 |
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
コンテナ内でも実行できる。

```bash
docker compose build vrc-ta-hub
docker compose up -d
docker exec vrc-ta-hub-app bash -c "cd /app && mypy event/services/ --config-file /pyproject.toml"
```

`docker-compose.yaml` が `./pyproject.toml` をコンテナの `/pyproject.toml` にマウント済みのため、
`--config-file /pyproject.toml` でホスト側の設定を使い回せる。

### 直接 mypy を呼ぶ場合

```bash
docker exec vrc-ta-hub-app mypy app/event/services/
```

設定は `pyproject.toml` の `[tool.mypy]` セクションから自動で読み込まれる。

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

## 既知のエラーと対処法

Phase 1 の初回実行で 5 件のエラーが出ている。それぞれの対処方針をメモしておく。

### 1. `Library stubs not installed` (bleach / markdown 等)

```
event/services/markdown_processor.py:13: error: Library stubs not installed for "bleach"  [import-untyped]
event/services/markdown_processor.py:14: error: Library stubs not installed for "markdown"  [import-untyped]
event/services/markdown_processor.py:15: error: Library stubs not installed for "bleach.css_sanitizer"  [import-untyped]
```

**対処**: `types-bleach` / `types-Markdown` を `requirements.txt` に追加する。
本 PR では「型スタブ追加 = 別関心事」なので含めず、フォロー PR で対応する。
急ぐ場合は `# type: ignore[import-untyped]` を import 行に付けて回避できる。

### 2. `Returning Any from function declared to return "str"`

```
event/services/markdown_processor.py:200: error: Returning Any from function declared to return "str"  [no-any-return]
```

**対処**: 第三者ライブラリ (`markdown.markdown()` 等) の戻り値が `Any` のため。
スタブを入れれば自然消滅するケースが多い。残った場合は `cast(str, ...)` で明示する。

### 3. OpenAI Completions.create の overload 不一致

```
event/services/content_generation_service.py:185: error: No overload variant of "create" of "Completions" matches argument types ...
```

**対処**: `openai` ライブラリの `client.chat.completions.create()` に渡している
`messages` の型 (`list[dict[str, str]]`) が SDK の overload と一致していない。
`ChatCompletionMessageParam` 型を使うか、`# type: ignore[call-overload]` で抑制する。
SDK 側のアップデートで解消することもある。

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

## CI 連携 (フォロー作業)

`.github/workflows/ci.yml` への mypy job 追加は本 PR には含めない。
理由: 本 PR は設定 + 観測手段の整備に集中し、CI 連携は別途レビューが必要なため。

追加するときの参考 YAML は `docs/tmp/w6-10-ci-yml-followup.md` を参照。

## 将来計画

1. **Phase 1 (現在)**: `event/services/` の warn-only 観測
2. **Phase 2**: `event/services/` で warn 0 達成
   - 既知エラー (型スタブ追加 / OpenAI 型問題 / Any 戻り値) を解消
3. **Phase 3**: `event/recurrence/`, `twitter/generators/` に範囲拡大
4. **Phase 4**: `disallow_untyped_defs = true` に昇格し、新規関数の未注釈を禁止
5. **Phase 5**: `strict = true` 相当に到達したアプリから、CI job を warn-only から
   blocking に切り替える

各 Phase は独立した PR で進める。一気に厳密化しない。
