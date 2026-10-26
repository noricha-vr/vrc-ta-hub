"""発表管理ガイドの各状態。生成・紐づけ完了は保存済みの架空例で示す。"""
from datetime import datetime, time, timezone

from django.db import transaction

from event.models import EventDetail
from tests.factories import make_event_detail


def seed(context):
    community = context['community']
    community.recording_allowed = True
    community.save(update_fields=['recording_allowed'])
    event = community.events.order_by('pk').first()
    presentation = event.details.get(theme='はじめてのワールド制作')
    transaction.on_commit(lambda: EventDetail.objects.filter(pk=presentation.pk).update(
        article_consent=EventDetail.ArticleConsent.OK,
        youtube_url='https://www.youtube.com/watch?v=GUIDEDEMO01',
        h1='はじめてのワールド制作',
        contents='## 小さく作って試す\n\nまず床と壁を置き、見え方を確かめます。\n\n## 振り返り\n\n一つずつ試すと、直す場所が分かります。',
    ))
    # 土台の作成通知が完了してから補助状態を追加する。
    # 土台のコールバックに、別の撮影状態の資料や発表を見せない。
    def add_link_states():
        # 未紐づけと紐づけ後は別のレコードで用意し、撮影から通知を発生させない。
        for linked, start in ((False, time(23)), (True, time(23, 30))):
            make_event_detail(
                event, speaker='アオイ', theme='小さな実験を始める', start_time=start,
                applicant=context['users']['speaker'] if linked else None, status='approved',
            )
        fixed = datetime(2030, 4, 1, 12, tzinfo=timezone.utc)
        EventDetail.objects.filter(event=event).update(created_at=fixed, updated_at=fixed)

    transaction.on_commit(add_link_states)
    fixed = datetime(2030, 4, 1, 12, tzinfo=timezone.utc)
    EventDetail.objects.filter(event=event).update(created_at=fixed, updated_at=fixed)
