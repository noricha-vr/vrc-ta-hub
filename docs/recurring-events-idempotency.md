# 定期イベント生成の冪等性

`generate_recurring_events` コマンドは、複数回連続実行されても Event の重複を生成せず、
途中で LLM が失敗した場合でも次回実行で残りを補完できる設計になっている。

## 冪等性の意義

定期イベント生成は Cloud Scheduler から定時実行される。以下のケースで重複や欠損が起き得る:

- 同じ時間帯にバッチが二重起動した
- LLM API（Gemini / OpenRouter）が一時的に失敗し、一部のルールのみ生成された
- 運用者が動作確認のため手動で `generate_recurring_events` を再実行した

これらの状況でも、コマンド自体は何度叩いても安全（=冪等）であることが求められる。

## どこで冪等性を担保しているか

`app/event/management/commands/generate_recurring_events.py` の中で、
日付ごとに以下の存在チェックをしている:

```python
exists = Event.objects.filter(
    community=community,
    date=date,
    start_time=community.start_time,
).exists()
if not exists:
    new_dates.append(date)
```

この `(community, date, start_time)` の組み合わせが既存 Event と重複する場合は新規作成しない。
したがって、**コマンドを何度実行しても同じ日付の Event は 1 件しか作成されない。**

## `RecurrenceRule.last_generated_date` の役割

`last_generated_date` は **進捗トラッキング用** のフィールドであり、重複防止そのものは
担っていない。次の用途に使う:

- どこまで自動生成が進んでいるかを管理画面で可視化する
- 監視で「last_generated_date が古すぎるルール」を検出して通知する
- LLM 失敗からのリカバリ判断に使う（失敗時は更新しないため、進捗が止まる）

### 更新タイミング

| ケース | last_generated_date の挙動 |
|--------|--------------------------|
| 新規 Event が 1 件以上作成された | 作成した日付の最大値で更新 |
| 新規 Event は 0 件だが、generate_dates が日付を返した（既に全件存在） | 既存日付の最大値で初期化（None のときのみ） |
| LLM 失敗で `generate_dates` が `[]` を返した | 更新しない（リカバリ判断のため進捗停止を残す） |
| `--dry-run` を指定した | 更新しない（DB 書き込み禁止のため） |

## 障害発生時のリカバリ手順

LLM 一時障害などで一部のルールでイベント生成が失敗した場合:

```bash
# もう一度叩くだけ
docker compose exec vrc-ta-hub python manage.py generate_recurring_events
```

`last_generated_date` が古いままのルールがあれば、それがリカバリ対象。
既に生成済みの Event は重複防止チェックでスキップされるため、副作用なしで再実行できる。
