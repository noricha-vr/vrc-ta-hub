import logging
from typing import Optional
from urllib.parse import urlencode

from event.models import Event
from twitter.models import TwitterTemplate
from twitter.tweet_generator import _call_llm

logger = logging.getLogger(__name__)


def format_event_info(event):
    """イベント情報を整形する"""
    # 曜日を計算
    weekdays = ['月', '火', '水', '木', '金', '土', '日']
    weekday = weekdays[event.date.weekday()]
    
    details = event.details.filter(status='approved').order_by('start_time')
    details_text = "\n".join([f"{d.start_time.strftime('%H:%M')} - {d.theme} ({d.speaker})" for d in details])
    return {
        "event_name": event.community.name,
        "date": f"{event.date.year}年{event.date.month}月{event.date.day}日({weekday})",
        "time": event.start_time.strftime("%H:%M"),
        "details": details_text
    }


def generate_tweet(template, event_info):
    """OpenRouter API経由でツイートを生成する。

    _call_llm に委譲して LLM 呼び出しコードの重複を排除する。
    """
    system_prompt = "あなたはイベント告知ツイートを作成する専門家です。"

    user_prompt = f"""過去のツイートのフォーマットに合わせて、イベントの告知ツイートを作成してください。
テンプレートのスタイルを模倣しつつ、与えられたイベント情報を自然に組み込んでください。

過去のツイート:
{template}

イベント情報:
イベント名: {event_info['event_name']}
日付: {event_info['date']}
時間: {event_info['time']}
詳細:
{event_info['details']}

生成するツイートは280文字以内にしてください。
ツイートのみを出力し、追加の説明は不要です。"""

    return _call_llm(system_prompt, user_prompt)


def generate_tweet_url(event: Event, template: TwitterTemplate) -> Optional[str]:
    """ツイート URL を生成する"""
    try:
        template = template.template
        event_info = format_event_info(event)
        tweet_text = generate_tweet(template, event_info)

        if tweet_text:
            tweet_url = f"https://twitter.com/intent/tweet?{urlencode({'text': tweet_text})}"
            return tweet_url
        else:
            return None
    except Exception as e:
        logger.error(f"Error generating tweet URL: {e}")
        return None
