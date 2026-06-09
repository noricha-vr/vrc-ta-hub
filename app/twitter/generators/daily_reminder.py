"""当日リマインダーポストの生成。

その日開催される集会の発表一覧を「今夜 HH:MM〜」の形でまとめる。
"""

import logging

from twitter.generators.common import (
    BODY_LINE_CONSTRAINT,
    _build_hashtag_suffix,
    _fit_candidate,
    _format_speaker_display,
    _sanitize_for_prompt,
)
from website.constants import build_site_url

logger = logging.getLogger(__name__)


def _fallback_daily_reminder_tweet(event) -> str | None:
    details = list(
        event.details.filter(
            status="approved",
            detail_type__in=("LT", "SPECIAL"),
        ).order_by("start_time", "pk")
    )
    if not details:
        return None

    community = event.community
    name = _sanitize_for_prompt(community.name)
    url = f"詳細はこちら {build_site_url(f'/community/{community.pk}/')}"
    hashtag_suffix = _build_hashtag_suffix(community)

    for count in range(min(3, len(details)), 0, -1):
        body_lines = [f"今夜 {event.start_time.strftime('%H:%M')}〜 {name}"]
        for detail in details[:count]:
            body_lines.append(
                f"{_format_speaker_display(detail)}"
                f"「{_sanitize_for_prompt(detail.theme)}」"
            )
        fitted = _fit_candidate(
            body_lines,
            url,
            hashtag_suffix,
            list(range(len(body_lines) - 1, -1, -1)),
        )
        if fitted:
            return fitted

    return None


def generate_daily_reminder_tweet(event, target_chars=140, validation_feedback="") -> str | None:
    """当日開催イベントのリマインダーポストを生成する。"""
    from twitter import tweet_generator

    approved_details = list(
        event.details.filter(
            status="approved",
            detail_type__in=("LT", "SPECIAL"),
        ).order_by("start_time", "pk")
    )
    if not approved_details:
        logger.warning("No approved LT/SPECIAL details found for Event %s", event.pk)
        return None

    community = event.community
    hashtag_suffix = _build_hashtag_suffix(community)
    community_url = build_site_url(f"/community/{community.pk}/")

    presentations = []
    for detail in approved_details[:3]:
        label = "発表" if detail.detail_type == "LT" else "特別回"
        speaker_display = _format_speaker_display(detail)
        theme = _sanitize_for_prompt(detail.theme)
        presentations.append(f"- {label}: {speaker_display}「{theme}」")

    more_count = len(approved_details) - len(presentations)
    extra_line = f"\n- ほか {more_count} 件" if more_count > 0 else ""

    system_prompt = (
        "あなたはVRChat集会の当日リマインダーポストを書くライターです。"
        "「誰が」「どんなテーマで」話すのかを主役にして、読んだ人が「聞きたい」と思える告知を書いてください。"
    )

    name = _sanitize_for_prompt(community.name)
    presentation_count = len(approved_details)
    user_prompt = f"""以下のイベント当日リマインダーポストを作成してください。

集会名: {name}
開催: 今夜 {event.start_time.strftime('%H:%M')}~
登録発表数: {presentation_count}件（※この数字は本文に書かない。背景情報として参照するだけ）
発表一覧:
{chr(10).join(presentations)}{extra_line}
{validation_feedback}

## 出力フォーマット（本文は3行以内、この構造に厳密に従うこと）

### 発表が1件の場合
今夜 {{時刻}}〜 {{集会名}}

{{登壇者表記1}}「{{テーマ1}}」
{{テーマ1の補足 + 参加誘導を1行に統合}}

詳細はこちら {{URL}}
{{ハッシュタグ}}

### 発表が2件の場合
今夜 {{時刻}}〜 {{集会名}}

{{登壇者表記1}}「{{テーマ1}}」
{{登壇者表記2}}「{{テーマ2}}」

詳細はこちら {{URL}}
{{ハッシュタグ}}

### 発表が3件以上の場合（4件以上なら上位3件＋「ほかN件」）
今夜 {{時刻}}〜 {{集会名}}
{{登壇者表記1}}「{{テーマ1}}」
{{登壇者表記2}}「{{テーマ2}}」／{{登壇者表記3}}「{{テーマ3}}」

詳細はこちら {{URL}}
{{ハッシュタグ}}

## ルール
- {target_chars}文字以内（URLやハッシュタグ含む。日本語は1文字としてカウント）
{BODY_LINE_CONSTRAINT}
- 1行目は「今夜 {event.start_time.strftime('%H:%M')}〜 {name}」の形式で、今日の開催であることと時刻が一目で伝わるようにする（日付表記は禁止）
- 各発表は発表一覧の登壇者表記をそのまま使い、「○○さん「テーマ名」」または「○○さん（@handle）「テーマ名」」の形式で記載する
  - テーマ名は発表一覧のものをそのまま使う（言い換え・要約・省略禁止）
  - Xアカウントがある登壇者は「（@handle）」も含める
- **発表数の言及禁止**: 「発表は1件」「全○件」「○本立て」「N件の発表」など、発表の本数を伝える表現は本文に一切書かない（通常1件なので情報価値がない）
- 発表が1件の場合は、発表行の直後に「テーマの補足 + 参加誘導」を**1行に統合**して入れる
  - 補足と誘導は別行に分けず、1文にまとめる（本文3行制約のため必須）
  - 補足は発表一覧に含まれるキーワードや背景知識から自然に膨らませる（事実の捏造は禁止）
- 発表が2件以上の場合は補足・誘導を省略し、発表行のみ並べる（本文3行に収めるため）
- 発表が4件以上の場合は上位3件を記載し、残りは「ほかN件」としてテーマ3の行末にまとめる
- 散文や自然文で発表内容をまとめない（一覧形式を崩さない）
- 末尾に以下を必ず含める:
  詳細はこちら {community_url}
  {hashtag_suffix}
- 空行の入れ方は**本文3行制約を最優先**に決める
  - 発表1件: 集会名の後・発表ブロックの後に空行（出力例の「### 発表が1件の場合」参照）
  - 発表2件以上: 本文行を詰めて配置（出力例の対応ブロック参照）。URL/ハッシュタグとの間にのみ空行を入れる
- ハッシュタグは末尾に指定されたもののみ使用（自分で追加・変形しない）
- 句点（。）を一切使わない
    - ポスト本文のみ出力（説明不要）
"""
    return tweet_generator._call_llm(system_prompt, user_prompt)
