"""イベント紹介 (過去発表資料の共有) ポスト生成。

公開済みスライド・動画への誘導ポストを LLM で生成する。
"""

from twitter.generators.common import (
    BODY_LINE_CONSTRAINT,
    _build_hashtag_suffix,
    _fit_candidate,
    _format_speaker_display,
    _sanitize_for_prompt,
)
from website.constants import build_site_url


def _fallback_slide_share_tweet(event_detail) -> str | None:
    community = event_detail.event.community
    speaker_display = _format_speaker_display(event_detail)
    resources = []
    if event_detail.slide_url or event_detail.slide_file:
        resources.append("スライド")
    if event_detail.youtube_url:
        resources.append("動画")
    resources_text = "・".join(resources) or "資料"
    body_lines = [
        (
            f"{_sanitize_for_prompt(community.name)} "
            f"{speaker_display}"
            f"「{_sanitize_for_prompt(event_detail.theme)}」"
        ),
        f"{resources_text}が公開されました",
    ]
    url = f"詳細はこちら {build_site_url(f'/event/detail/{event_detail.pk}/')}"
    return _fit_candidate(body_lines, url, _build_hashtag_suffix(community), [0])


def generate_slide_share_tweet(event_detail, target_chars=140, validation_feedback="") -> str | None:
    """スライド/記事共有ポストを生成する。

    Args:
        event_detail: EventDetail モデルインスタンス（slide_url または youtube_url が設定済み）
        target_chars: LLM に指示する目標文字数
    """
    from twitter import tweet_generator

    system_prompt = (
        "あなたはVRChat集会の発表資料を紹介するライターです。"
        "「この資料、読んでみたい」と思わせるポストを書いてください。"
    )

    event = event_detail.event
    community = event.community
    hashtag_suffix = _build_hashtag_suffix(community)
    detail_url = build_site_url(f"/event/detail/{event_detail.pk}/")

    name = _sanitize_for_prompt(community.name)
    speaker_display = _format_speaker_display(event_detail)
    theme = _sanitize_for_prompt(event_detail.theme)

    # 公開リソースの種類をフラグで判定（URLはプロンプトに含めない）
    resources = []
    if event_detail.slide_url or event_detail.slide_file:
        resources.append("スライド")
    if event_detail.youtube_url:
        resources.append("動画")
    resources_text = "・".join(resources)

    user_prompt = f"""以下の発表の{resources_text}が公開されたことを伝えるポストを作成してください。

集会名: {name}
発表者: {speaker_display}
テーマ: {theme}
公開された資料: {resources_text}
{validation_feedback}

## 必須要素（必ず本文に含めること）
1. 集会名（「{name}」）
2. 発表者（「{speaker_display}」をそのまま記載）
3. 発表テーマ（「{theme}」をそのまま記載。言い換え・要約禁止）
4. {resources_text}が公開されたこと
5. 内容の補足と次のアクション（資料を見る・チェックする等）への誘導を**1行にまとめる**（本文3行制約のため別行にしない）

## 出力フォーマット（本文は3行以内）

{{集会名}} {{発表者}}「{{テーマ}}」

{{resources_text}}が公開されました
{{内容補足 + 閲覧誘導を1行に統合}}

詳細はこちら {{URL}}
{{ハッシュタグ}}

## スタイル
- {target_chars}文字以内（URLやハッシュタグ含む。日本語は1文字としてカウント）
{BODY_LINE_CONSTRAINT}
- 日付は不要（過去のイベントなので）
- テーマ名をそのまま書いた上で、「読むと何がわかるか」を1文で補足する
- 誘導の一文は毎回異なる自然な表現にする（「〜な方はチェック」のような定型文の繰り返し禁止）
- 末尾に以下を必ず含める:
  詳細はこちら {detail_url}
  {hashtag_suffix}
- 意味のまとまり（日時・テーマ・補足・リンク・ハッシュタグ）ごとに空行を入れて読みやすくする
- ハッシュタグは末尾に指定されたもののみ使用（自分で追加・変形しない）
- 句点（。）を一切使わない（「〜です。」「〜ます。」も禁止。「〜です」「〜ます」で止める）
    - ポスト本文のみ出力（説明不要）
"""
    return tweet_generator._call_llm(system_prompt, user_prompt)
