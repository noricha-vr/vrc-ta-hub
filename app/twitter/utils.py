import logging
from typing import Optional
from urllib.parse import urlencode

from django.conf import settings
from openai import OpenAI

from event.models import Event
from twitter.models import TwitterTemplate

logger = logging.getLogger(__name__)
# OpenAI API の設定
client = OpenAI(api_key=settings.OPENAI_API_KEY)


def format_event_info(event):
    """イベント情報を整形する"""
    details = event.details.all().order_by('start_time')
    details_text = "\n".join([f"{d.start_time.strftime('%H:%M')} - {d.theme} ({d.speaker})" for d in details])
    return {
        "event_name": event.community.name,
        "date": event.date.strftime("%Y年%m月%d日"),
        "time": event.start_time.strftime("%H:%M"),
        "details": details_text
    }


def generate_tweet(template, event_info):
    """OpenAI APIを使用してツイートを生成する"""
    prompt = f"""
    過去のツイートのフォーマットに合わせて、イベントの告知ツイートを作成してください。
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
    ツイートのみを出力し、追加の説明は不要です。
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "あなたはイベント告知ツイートを作成する専門家です。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=280
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Error generating tweet: {e}")
        return None


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
