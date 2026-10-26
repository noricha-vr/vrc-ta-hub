"""イベント管理と告知の撮影用状態。架空データだけを使う。"""
from datetime import datetime, time, timedelta

from django.utils import timezone

from event.models import EventDetail
from tests.factories import make_event
from twitter.models import TweetQueue


def seed(context):
    community = context['community']
    base = context['base_date']
    parent = community.events.get(is_recurring_master=True)
    # 承認済みの発表で、資料・未紐づけの操作も地図に載せる。
    EventDetail.objects.filter(event=parent, status='approved').update(
        applicant=None, slide_file='guide-demo/slides.pdf',
    )
    for days in (14, 21):
        make_event(community, event_date=base + timedelta(days=days),
                   recurring_master=parent)
    make_event(community, event_date=base - timedelta(days=3653))
    stamp = timezone.make_aware(datetime.combine(base, time(9)))
    # bulk_create で外部処理を起動せず、予約の表示だけを用意する。
    queue = TweetQueue(
        community=community, event=parent, tweet_type='daily_reminder',
        status='ready', scheduled_at=stamp + timedelta(days=7, hours=10),
        generated_text='4月8日 22:00から さくら技術交流会\n'
                       'アオイさん「はじめてのワールド制作」\n'
                       '#さくら技術交流会 #VRChat技術学術',
    )
    existing = TweetQueue.objects.filter(event=parent, tweet_type='daily_reminder')
    if not existing.exists():
        TweetQueue.objects.bulk_create([queue])
    existing.update(status='ready', generated_text=queue.generated_text,
                    scheduled_at=queue.scheduled_at, created_at=stamp, error_message='')
