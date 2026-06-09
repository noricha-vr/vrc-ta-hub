"""新規集会の告知ポスト生成。

新しく登録された集会の最初の告知。初回開催日が決まっていれば日程も含める。
"""

from twitter.generators.common import (
    WEEKDAY_NAMES,
    BODY_LINE_CONSTRAINT,
    _build_hashtag_suffix,
    _fit_candidate,
    _format_weekdays,
    _sanitize_for_prompt,
)
from website.constants import build_site_url


def _fallback_new_community_tweet(community, first_event=None) -> str | None:
    weekdays_str = _format_weekdays(community.weekdays)
    name = _sanitize_for_prompt(community.name)
    url = f"詳細はこちら {build_site_url(f'/community/{community.pk}/')}"
    hashtag_suffix = _build_hashtag_suffix(community)

    if first_event:
        weekday = WEEKDAY_NAMES.get(first_event.date.strftime("%a"), "")
        schedule = f"{first_event.date.strftime('%-m/%-d')}({weekday}) {first_event.start_time.strftime('%H:%M')}~"
    else:
        schedule = f"{community.frequency} {weekdays_str}曜日 {community.start_time.strftime('%H:%M')}~"

    body_lines = [
        f"新しい集会「{name}」がはじまります",
        schedule,
    ]
    return _fit_candidate(body_lines, url, hashtag_suffix, [0])


def generate_new_community_tweet(
    community,
    first_event=None,
    target_chars=140,
    validation_feedback="",
) -> str | None:
    """新規集会の告知ポストを生成する。

    Args:
        community: Community モデルインスタンス
        first_event: 初回 Event (あれば日程を含める)
        target_chars: LLM に指示する目標文字数
    """
    from twitter import tweet_generator

    system_prompt = (
        "あなたはVRChat技術学術系集会の告知ポストを作成するライターです。"
        "「参加したい」と思わせる告知を書いてください。"
    )

    weekdays_str = _format_weekdays(community.weekdays)

    event_info = ""
    if first_event:
        event_info = (
            f"\n初回開催日: {first_event.date.strftime('%-m/%-d')}({WEEKDAY_NAMES.get(first_event.date.strftime('%a'), '')})"
            f" {first_event.start_time.strftime('%H:%M')}~"
        )

    hashtag_suffix = _build_hashtag_suffix(community)
    community_url = build_site_url(f"/community/{community.pk}/")
    name = _sanitize_for_prompt(community.name)
    description = _sanitize_for_prompt(community.description) or "(なし)"

    user_prompt = f"""以下の新しいVRChat集会の告知ポストを作成してください。

集会名: {name}
開催: {community.frequency} {weekdays_str}曜日 {community.start_time.strftime('%H:%M')}~
紹介: {description}{event_info}
{validation_feedback}

## 必須要素（必ず本文に含めること）
1. 集会名
2. 開催スケジュール（曜日・時刻）
3. どんな人向けか / 何が得られるか（紹介文から1行で）

## 出力フォーマット（本文は3行以内）

新しい集会「{{集会名}}」がはじまります

{{開催スケジュール}}
{{対象者 + 何が得られるかを1行に統合}}

詳細はこちら {{URL}}
{{ハッシュタグ}}

## スタイル
- {target_chars}文字以内（URLやハッシュタグ含む。日本語は1文字としてカウント）
{BODY_LINE_CONSTRAINT}
- 「こんな集会が始まりました」ではなく「こういう人は来て」というトーン
- 末尾に以下を必ず含める:
  詳細はこちら {community_url}
  {hashtag_suffix}
- 意味のまとまり（日時・テーマ・補足・リンク・ハッシュタグ）ごとに空行を入れて読みやすくする
- ハッシュタグは末尾に指定されたもののみ使用（自分で追加・変形しない）
- 句点（。）を一切使わない（「〜です。」「〜ます。」も禁止。「〜です」「〜ます」で止める）
    - ポスト本文のみ出力（説明不要）
"""
    return tweet_generator._call_llm(system_prompt, user_prompt)
