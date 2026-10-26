"""外部連携ガイドだけで使う架空の設定。"""
from datetime import datetime, timezone
from pathlib import Path

from django.core.files.base import ContentFile

from event_calendar.models import CalendarEntry
from twitter.models import TwitterTemplate
from user_account.models import APIKey


def seed(context):
    community = context['community']
    community.allow_poster_repost = True
    poster = Path(__file__).resolve().parents[1] / 'static/guide/images/posters/vrc-ta-hub-poster-1.jpg'
    community.poster_image.save('guide-demo-poster.jpg', ContentFile(poster.read_bytes()), save=False)
    community.save(update_fields=['allow_poster_repost', 'poster_image'])
    CalendarEntry.objects.create(
        community=community, event_detail='ワールド制作の工夫を発表して学ぶ交流会です。',
        event_genres=['ACADEMIC_EVENT', 'BEGINNER_EVENT'], join_condition='初めての方も参加できます。',
        how_to_join='集会の案内にある VRChat グループから参加してください。',
        note='発表中はマイクをミュートにしてください。',
        x_post_text='さくら技術交流会を開催します。初めての方も歓迎します。',
    )
    TwitterTemplate.objects.create(
        community=community, name='いつもの告知',
        template='{date} {time}から{event_name}を開催します。\n{theme}\n初めての方も歓迎します。\n{group_url}\n{hashtag}',
    )
    # 一覧用。生キーは保存せず、表示されるハッシュも撮影時に伏せる。
    key = APIKey.objects.create(user=context['users']['owner'], name='資料連携用', key='0' * 64)
    APIKey.objects.filter(pk=key.pk).update(created_at=datetime(2030, 4, 1, 12, tzinfo=timezone.utc))
