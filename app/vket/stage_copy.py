"""ステージ告知フォームへ貼り付ける内容を組み立てる。"""

from datetime import datetime, timedelta

from django.urls import reverse

from event.models import _strip_markdown
from website.constants import build_site_url

from .models import VketPresentation

SCHEDULE_PENDING = '日程の確定後に表示します'
WEEKDAYS = '月火水木金土日'


def _shorten(text: str, limit: int) -> str:
    """長文は先頭を残し、可能なら句点で切る。"""
    if len(text) <= limit:
        return text
    if limit <= 1:
        return ''
    head = text[:limit - 1]
    stop = head.rfind('。')
    if stop >= len(head) // 2:
        return head[:stop + 1]
    return head.rstrip() + '…'


def _plain_paragraphs(text: str) -> str:
    """マークダウンを落としつつ、紹介文の改行は残す（空行は 1 つにまとめる）。"""
    lines = [_strip_markdown(line) for line in (text or '').replace('\r\n', '\n').split('\n')]
    paragraphs = '\n'.join(lines)
    while '\n\n\n' in paragraphs:
        paragraphs = paragraphs.replace('\n\n\n', '\n\n')
    return paragraphs.strip()


def stage_schedule(participation) -> tuple[datetime | None, datetime | None]:
    """希望日時を混ぜず、確定した開始・終了日時を返す。"""
    if (participation.confirmed_date is None or participation.confirmed_start_time is None
            or participation.confirmed_duration is None):
        return None, None
    start = datetime.combine(participation.confirmed_date, participation.confirmed_start_time)
    return start, start + timedelta(minutes=participation.confirmed_duration)


def thumbnail_prompt(participation, *, for_edit=False) -> str:
    """画像内の文字を集会名と日時に絞った指示文を返す。"""
    start, _ = stage_schedule(participation)
    date_text = (
        f'{start.month}/{start.day}({WEEKDAYS[start.weekday()]}) {start:%H:%M}'
        if start else '日程調整中'
    )
    source = '添付の生成した画像とポスターを参考に' if for_edit else '添付のポスターを参考に'
    prompt = (
        '直したい点：【ここに書く（例：キャラクターをもう少し大きく、背景を夜にする）】\n'
        if for_edit else ''
    ) + (
        f'{source}、画風・色・キャラクターのデザインをそのまま使い、'
        '16:9（1536×864 など）の横長のイベント告知サムネイルを1枚作ってください。'
        'キャラクターは左側に置き、キャラクターのデザインは変えないでください。'
        '配置だけを横長に組み替えてください。'
        f'画像内の文字は「{participation.community.name}」と「{date_text}」だけにしてください。'
        '文字の誤字を確かめてください。'
    )
    themes = list(participation.presentations.filter(
        status=VketPresentation.Status.CONFIRMED,
    ).exclude(theme='').order_by('order', 'id').values_list('theme', flat=True))
    if themes:
        prompt += '当日の発表テーマ（雰囲気づけだけに使い、画像内の文字にはしない）：' + '、'.join(themes)
    return prompt


def build_stage_copy(participation, guidance: dict) -> dict:
    """紹介文より登録に必要な日時・リンクを優先し、本文を1000文字以内に収める。"""
    community = participation.community
    collaboration = participation.collaboration
    settings = collaboration.settings_json
    config = settings.get('stage_copy', {}) if isinstance(settings, dict) else {}
    config = config if isinstance(config, dict) else {}

    def setting_text(key):
        value = config.get(key)
        return _strip_markdown(value) if isinstance(value, str) else ''

    short_name = setting_text('title_suffix') or _strip_markdown(collaboration.name)
    community_url = build_site_url(reverse('community:detail', kwargs={'pk': community.pk}))
    schedule_url = build_site_url(reverse('vket:detail', kwargs={'pk': collaboration.pk}))
    venue = guidance.get('venue', '')
    start, end = stage_schedule(participation)
    date_text = SCHEDULE_PENDING
    if start:
        end_text = f'{end:%H:%M}' if start.date() == end.date() else f'{end.month}月{end.day}日 {end:%H:%M}'
        date_text = f'{start.month}月{start.day}日（{WEEKDAYS[start.weekday()]}）{start:%H:%M}〜{end_text}'

    presentations = participation.presentations.filter(
        status=VketPresentation.Status.CONFIRMED,
    ).exclude(speaker='', theme='').order_by('order', 'id')
    content = '\n'.join(
        f'{_strip_markdown(p.speaker)} - {_strip_markdown(p.theme)}' for p in presentations
    ) or '発表者とテーマは決まりしだいお知らせします。'
    hashtags = config.get('hashtags') or collaboration.hashtags or ''
    if isinstance(hashtags, list):
        hashtags = ' '.join(tag for tag in hashtags if isinstance(tag, str))
    hashtags = hashtags if isinstance(hashtags, str) else ''
    hashtags = ' '.join(dict.fromkeys(
        f'#{tag.lstrip("#＃")}' for tag in f'{community.twitter_hashtag} {hashtags}'.split() if tag.lstrip('#＃')
    ))
    links = [f'集会ページ：{community_url}']
    if community.sns_url:
        links.append(f'X：{community.sns_url}')
    if community.discord:
        links.append(f'Discord：{community.discord}')

    intro = _shorten(_plain_paragraphs(community.description), 300)
    week = _shorten(setting_text('week_description'), 200)
    content = _shorten(content, 350)

    def body():
        sections = [intro, f'■ 日時\n{date_text}', f'■ 会場\n{_shorten(_strip_markdown(venue), 120)} のステージ', f'■ 内容\n{content}']
        if setting_text('week_description'):
            sections.append(f'■ {_shorten(short_name, 80)}とは\n{week}\nスケジュール：{schedule_url}')
        else:
            sections.append(f'■ {_shorten(short_name, 80)}\nスケジュール：{schedule_url}')
        sections.extend([f'■ {community.name}について\n' + '\n'.join(links), _shorten(hashtags, 120)])
        return '\n\n'.join(section for section in sections if section)

    # 長い紹介・企画説明・発表一覧の順で減らし、通常の本文ではリンクを残す。
    overflow = max(0, len(body()) - 1000)
    intro = _shorten(intro, max(0, len(intro) - overflow))
    overflow = max(0, len(body()) - 1000)
    week = _shorten(week, max(0, len(week) - overflow))
    overflow = max(0, len(body()) - 1000)
    content = _shorten(content, max(0, len(content) - overflow))
    text = _shorten(body(), 1000)
    fields = [
        {'id': 'url', 'label': 'URL', 'value': community_url},
        {'id': 'title', 'label': 'タイトル', 'value': f'{community.name} ｜ {short_name}'},
        {'id': 'body', 'label': '本文', 'value': text, 'editable': True},
        {'id': 'start', 'label': '開始日時', 'value': f'{start:%Y/%m/%d %H:%M}' if start else SCHEDULE_PENDING},
        {'id': 'end', 'label': '終了日時', 'value': f'{end:%Y/%m/%d %H:%M}' if end else SCHEDULE_PENDING},
        {'id': 'language', 'label': '言語', 'value': '日本語'},
        {'id': 'venue', 'label': '開催会場', 'value': venue or '運営からの案内をご確認ください'},
        {'id': 'tag', 'label': 'タグ', 'value': guidance.get('tag') or '運営からの案内をご確認ください'},
    ]
    return {'copy_fields': fields, 'edit_prompt': thumbnail_prompt(participation, for_edit=True)}
