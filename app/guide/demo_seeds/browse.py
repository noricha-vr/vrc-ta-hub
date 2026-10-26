"""検索・カレンダー用の公開デモ。画像も一時 MEDIA_ROOT 内だけに保存する。"""
import io
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from PIL import Image, ImageDraw, ImageFont

from news.models import Category, Post
from event.models import Event
from tests.factories import make_community, make_event_detail


def demo_image(title, size=(640, 900)):
    image = Image.new('RGB', size, '#eef5ff')
    draw = ImageDraw.Draw(image)
    font_path = Path('/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc')
    font = ImageFont.truetype(str(font_path), 32) if font_path.exists() else ImageFont.load_default()
    draw.rounded_rectangle((24, 24, size[0] - 24, size[1] - 24), radius=24, outline='#3272ac', width=6)
    draw.multiline_text((50, 90), title, font=font, fill='#234663', spacing=24)
    buffer = io.BytesIO()
    image.save(buffer, 'JPEG')
    return ContentFile(buffer.getvalue())


def seed(context):
    if not getattr(settings, 'GUIDE_CAPTURE_EXTENDED_DEMO', False):
        return
    community = context['community']
    community.poster_image.save('demo.jpg', demo_image('さくら技術交流会\n\n毎週 月曜日 22:00\n\n技術を楽しもう\n\nデモ用の架空の集会'), save=False)
    community.allow_poster_repost = True
    community.frequency = '毎週月曜日'
    community.save()
    past = Event.objects.get(community=community, date=context['base_date'] - timedelta(days=7))
    make_event_detail(past, speaker='ヒナ', theme='身近な科学を話そう', status='approved')
    make_event_detail(past, speaker='さくら', theme='春の交流企画', detail_type='SPECIAL',
                      status='approved', contents='みんなで学んだことを紹介する架空の特別企画です。')
    make_event_detail(past, speaker='レン', theme='集会を開いて学んだこと', detail_type='BLOG',
                      status='approved', contents='初めての集会を振り返る架空の記事です。')
    # 共通の未承認集会は変更せず、独立したアーカイブを足す。
    archived = make_community(name='こはるの学びの会', owner=context['users']['staff'], organizers='こはる',
                              created_at=context['base_date'] - timedelta(days=365),
                              end_at=context['base_date'] - timedelta(days=30))
    archived.poster_image.save('archive.jpg', demo_image('こはるの学びの会\n\n活動の記録\n\nデモ用の架空の集会'), save=False)
    archived.save()
    category, _ = Category.objects.get_or_create(slug='guide-demo', defaults={'name': '使い方のお知らせ'})
    post = Post.objects.create(title='デモのお知らせを読んでみよう', slug='guide-demo-news',
                        category=category, body_markdown='これはガイド撮影用の架空のお知らせです。',
                        is_published=True, published_at='2030-03-30T03:00:00Z')
    post.thumbnail.save('demo-news.jpg', demo_image('デモのお知らせ\n\nガイド撮影用', (960, 540)), save=True)
