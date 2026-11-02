"""ヒーローは画像と再生リンクを統合し、iframe は再生時にだけ生成する。"""
from datetime import date, time

from django.core.files.base import ContentFile
from django.test import Client, TestCase
from django.urls import reverse

from event.models import EventDetail
from tests.factories import make_community, make_event, make_event_detail

# 1x1 の最小 PNG。ImageField のバリデーションを通すためだけに使う
MINIMAL_PNG = bytes.fromhex(
    '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4'
    '890000000a49444154789c6300010000050001'
    '0d0a2db40000000049454e44ae426082'
)


class EventDetailMediaOrderTests(TestCase):
    """初期表示の画像と動画の再生導線を検証する。"""

    @classmethod
    def setUpTestData(cls):
        cls.community = make_community(
            name='順序検証集会', frequency='毎週', organizers='主催',
        )
        cls.event = make_event(
            cls.community,
            event_date=date(2026, 2, 10),
            start_time=time(22, 0),
            weekday='Tue',
        )

    def _create_detail(self, *, with_video: bool, with_thumbnail: bool) -> EventDetail:
        detail = make_event_detail(
            self.event,
            status='approved',
            speaker='Speaker',
            theme=f'video={with_video} thumb={with_thumbnail}',
            start_time=time(22, 0),
            contents='contents',
            youtube_url='https://www.youtube.com/watch?v=dQw4w9WgXcQ' if with_video else '',
        )
        if with_thumbnail:
            detail.thumbnail_image.save(
                f'order-test-{detail.pk}.png', ContentFile(MINIMAL_PNG), save=True
            )
        return detail

    def _get_html(self, detail: EventDetail) -> str:
        response = Client().get(reverse('event:detail', kwargs={'pk': detail.pk}))
        self.assertEqual(response.status_code, 200)
        return response.content.decode('utf-8')

    def test_thumbnail_and_video_render_one_hero_without_iframe(self):
        html = self._get_html(self._create_detail(with_video=True, with_thumbnail=True))
        self.assertIn('event-detail-thumbnail img-fluid', html)
        self.assertIn('class="ed-play"', html)
        self.assertIn('https://www.youtube.com/watch?v=dQw4w9WgXcQ', html)
        self.assertNotIn('<iframe', html)
        self.assertNotIn('/maxresdefault.jpg', html)

    def test_video_only_uses_youtube_image_and_play_link(self):
        html = self._get_html(self._create_detail(with_video=True, with_thumbnail=False))
        self.assertIn('https://img.youtube.com/vi/dQw4w9WgXcQ/maxresdefault.jpg', html)
        self.assertIn('/hqdefault.jpg', html)
        self.assertIn('this.onerror=null', html)
        self.assertIn('class="ed-play"', html)
        self.assertNotIn('<iframe', html)

    def test_thumbnail_only_has_no_play_link(self):
        html = self._get_html(self._create_detail(with_video=False, with_thumbnail=True))
        self.assertIn('event-detail-thumbnail img-fluid', html)
        self.assertNotIn('class="ed-play"', html)
        self.assertNotIn('<iframe', html)

    def test_no_images_has_no_hero(self):
        html = self._get_html(self._create_detail(with_video=False, with_thumbnail=False))
        self.assertNotIn('class="ed-media', html)
        self.assertNotIn('<iframe', html)

    def test_play_link_preserves_start_time_without_javascript(self):
        detail = self._create_detail(with_video=True, with_thumbnail=False)
        detail.youtube_url += '&t=1m30s'
        detail.save(update_fields=['youtube_url'])
        html = self._get_html(detail)
        self.assertIn('watch?v=dQw4w9WgXcQ&amp;t=90', html)
        self.assertIn('data-start-time="90"', html)
