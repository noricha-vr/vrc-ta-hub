"""集会ガイド用。外部 API を呼ばず、一時 SQLite とローカル画像だけを使う。"""
from datetime import date, datetime, timezone

from django.core.files.base import ContentFile
from PIL import Image, ImageDraw

from tests.factories import make_community


def seed(context):
    import io

    users = context['users']
    base = context['base_date']
    community = context['community']
    community.frequency = '毎週月曜日'
    community.accepts_lt_application = True
    community.recording_allowed = True
    community.save()
    poster = Image.new('RGB', (420, 594), '#e9f5f0')
    draw = ImageDraw.Draw(poster)
    draw.ellipse((55, 90, 365, 400), fill='#96cdb8')
    draw.rectangle((85, 440, 335, 465), fill='#35715e')
    output = io.BytesIO()
    poster.save(output, 'PNG')
    community.poster_image.save('guide-demo-poster.png', ContentFile(output.getvalue()))
    make_community(name='ヒナの科学交流会', owner=users['general'], status='pending',
                   organizers='ヒナ', frequency='毎週月曜日', created_at=base, tags=['academic'])
    make_community(name='こもれび技術勉強会', owner=users['owner'], organizers='さくら',
                   frequency='毎月第1月曜日', created_at=base, tags=['tech'])
    make_community(name='ひだまり学習会', owner=users['owner'], organizers='さくら',
                   frequency='', weekdays=[], created_at=date(2020, 4, 1),
                   end_at=date(2020, 4, 1), tags=['tech'])
    fixed = datetime(2030, 4, 1, 12, tzinfo=timezone.utc)
    for user in users.values():
        type(user).objects.filter(pk=user.pk).update(date_joined=fixed)
