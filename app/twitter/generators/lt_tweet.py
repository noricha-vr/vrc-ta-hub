"""発表 (LT) / 特別回告知ポストの生成。

LLM 呼び出し本体 (`_call_llm`) は `twitter.tweet_generator` 経由で参照する。
これは `@patch("twitter.tweet_generator._call_llm")` を使う既存テストとの後方互換性のため。
"""

from twitter.generators.common import (
    WEEKDAY_NAMES,
    BODY_LINE_CONSTRAINT,
    _build_hashtag_suffix,
    _fit_candidate,
    _format_speaker_display,
    _sanitize_for_prompt,
)
from website.constants import build_site_url


def _fallback_presentation_tweet(event_detail, *, special: bool = False) -> str | None:
    event = event_detail.event
    community = event.community
    weekday = WEEKDAY_NAMES.get(event.date.strftime("%a"), "")
    label = " 特別回" if special else ""
    speaker_display = _format_speaker_display(event_detail)
    body_lines = [
        f"{event.date.strftime('%-m/%-d')}({weekday}) {event.start_time.strftime('%H:%M')}~ {_sanitize_for_prompt(community.name)}{label}",
        f"{speaker_display}「{_sanitize_for_prompt(event_detail.theme)}」",
    ]
    url = f"詳細はこちら {build_site_url(f'/community/{community.pk}/')}"
    return _fit_candidate(body_lines, url, _build_hashtag_suffix(community), [1, 0])


def generate_lt_tweet(event_detail, target_chars=140, validation_feedback="") -> str | None:
    """発表告知ポストを生成する。

    Args:
        event_detail: EventDetail モデルインスタンス (detail_type='LT')
        target_chars: LLM に指示する目標文字数
    """
    # _call_llm を遅延 import (シム側 namespace を参照して @patch を有効にする)
    from twitter import tweet_generator

    system_prompt = (
        "あなたはVRChat集会の発表告知ポストを書くライターです。"
        "読んだ人が「聞きたい」「行きたい」と思う告知を書いてください。"
    )

    event = event_detail.event
    community = event.community
    hashtag_suffix = _build_hashtag_suffix(community)
    community_url = build_site_url(f"/community/{community.pk}/")
    weekday = WEEKDAY_NAMES.get(event.date.strftime("%a"), "")

    name = _sanitize_for_prompt(community.name)
    speaker_display = _format_speaker_display(event_detail)
    theme = _sanitize_for_prompt(event_detail.theme)

    user_prompt = f"""以下の発表の告知ポストを作成してください。

集会名: {name}
日時: {event.date.strftime('%-m/%-d')}({weekday}) {event.start_time.strftime('%H:%M')}~
発表者: {speaker_display}
テーマ: {theme}
{validation_feedback}

## 必須要素（必ず本文に含めること）
1. 集会名（「{name}」）
2. 開催日時（「{event.date.strftime('%-m/%-d')}({weekday}) {event.start_time.strftime('%H:%M')}~」の形式で）
3. 発表テーマ（「{theme}」をそのまま記載。言い換え・要約禁止）
4. 発表者（「{speaker_display}」をそのまま記載）
5. テーマの補足説明と次のアクション（聞きに来る・詳細を見る等）への誘導を**1行にまとめる**（本文3行制約のため別行にしない）

## 出力フォーマット（本文は3行以内）

{{日時}} {{集会名}}

{{発表者}}「{{テーマ}}」
{{テーマ補足 + 参加誘導を1行に統合}}

詳細はこちら {{URL}}
{{ハッシュタグ}}

## スタイル
- {target_chars}文字以内（URLやハッシュタグ含む。日本語は1文字としてカウント）
{BODY_LINE_CONSTRAINT}
- テーマ名をそのまま書いた上で、何が聞けるかを1文で補足する
- 誘導の一文は毎回異なる自然な表現にする（「このテーマが気になる人は聞きに来て」のような定型文の繰り返し禁止）
- 末尾に以下を必ず含める:
  詳細はこちら {community_url}
  {hashtag_suffix}
- 意味のまとまり（日時・テーマ・補足・リンク・ハッシュタグ）ごとに空行を入れて読みやすくする
- ハッシュタグは末尾に指定されたもののみ使用（自分で追加・変形しない）
- 句点（。）を一切使わない（「〜です。」「〜ます。」も禁止。「〜です」「〜ます」で止める）
    - ポスト本文のみ出力（説明不要）
"""
    return tweet_generator._call_llm(system_prompt, user_prompt)


def generate_special_event_tweet(event_detail, target_chars=140, validation_feedback="") -> str | None:
    """特別回告知ポストを生成する。

    Args:
        event_detail: EventDetail モデルインスタンス (detail_type='SPECIAL')
        target_chars: LLM に指示する目標文字数
    """
    from twitter import tweet_generator

    system_prompt = (
        "あなたはVRChat集会の特別イベント告知ポストを書くライターです。"
        "通常回とは違う特別な回であることを伝え、「行きたい」と思わせてください。"
    )

    event = event_detail.event
    community = event.community
    hashtag_suffix = _build_hashtag_suffix(community)
    community_url = build_site_url(f"/community/{community.pk}/")
    weekday = WEEKDAY_NAMES.get(event.date.strftime("%a"), "")

    name = _sanitize_for_prompt(community.name)
    speaker = _sanitize_for_prompt(event_detail.speaker)
    theme = _sanitize_for_prompt(event_detail.theme)

    user_prompt = f"""以下の特別イベントの告知ポストを作成してください。

集会名: {name}
日時: {event.date.strftime('%-m/%-d')}({weekday}) {event.start_time.strftime('%H:%M')}~
発表者/ゲスト: {speaker}
テーマ: {theme}
{validation_feedback}

## 必須要素（必ず本文に含めること）
1. 集会名（「{name}」）
2. 「特別回」であること
3. 開催日時（「{event.date.strftime('%-m/%-d')}({weekday}) {event.start_time.strftime('%H:%M')}~」の形式で）
4. 発表テーマ（「{theme}」をそのまま記載。言い換え・要約禁止）
5. 発表者/ゲスト名（敬称は「さん」を付ける）
6. テーマの補足説明と次のアクション（聞きに来る・詳細を見る等）への誘導を**1行にまとめる**（本文3行制約のため別行にしない）

## 出力フォーマット（本文は3行以内）

{{日時}} {{集会名}} 特別回

{{発表者}}さん「{{テーマ}}」
{{テーマ補足 + 参加誘導を1行に統合}}

詳細はこちら {{URL}}
{{ハッシュタグ}}

## スタイル
- {target_chars}文字以内（URLやハッシュタグ含む。日本語は1文字としてカウント）
{BODY_LINE_CONSTRAINT}
- テーマ名をそのまま書いた上で、特別回ならではの見どころを1文で補足する
- 誘導の一文は毎回異なる自然な表現にする（「このテーマに興味ある人は来て」のような定型文の繰り返し禁止）
- 末尾に以下を必ず含める:
  詳細はこちら {community_url}
  {hashtag_suffix}
- 意味のまとまり（日時・テーマ・補足・リンク・ハッシュタグ）ごとに空行を入れて読みやすくする
- ハッシュタグは末尾に指定されたもののみ使用（自分で追加・変形しない）
- 句点（。）を一切使わない（「〜です。」「〜ます。」も禁止。「〜です」「〜ます」で止める）
    - ポスト本文のみ出力（説明不要）
"""
    return tweet_generator._call_llm(system_prompt, user_prompt)
