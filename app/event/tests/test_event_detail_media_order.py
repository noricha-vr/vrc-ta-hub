"""イベント詳細ページのサムネイル画像とYouTube動画の表示順序テスト.

サムネイルと動画の内容がほぼ同じ絵になるため、上部にサムネイル・下部に動画を置く。
レンダリング結果の HTML 上での出現順で検証する（Issue #599）。
"""
from datetime import date, time

from django.core.files.base import ContentFile
from django.test import Client, TestCase
from django.urls import reverse

from community.models import Community
from event.models import Event, EventDetail

# 1x1 の最小 PNG。ImageField のバリデーションを通すためだけに使う
MINIMAL_PNG = bytes.fromhex(
    '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4'
    '890000000a49444154789c6300010000050001'
    '0d0a2db40000000049454e44ae426082'
)


class EventDetailMediaOrderTests(TestCase):
    """サムネイル画像がYouTube動画より先に表示される."""

    @classmethod
    def setUpTestData(cls):
        cls.community = Community.objects.create(
            name='順序検証集会', status='approved', frequency='毎週', organizers='主催',
        )
        cls.event = Event.objects.create(
            community=cls.community,
            date=date(2026, 2, 10),
            start_time=time(22, 0),
            duration=60,
            weekday='Tue',
        )

    def _create_detail(self, *, with_video: bool, with_thumbnail: bool) -> EventDetail:
        detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            start_time=time(22, 0),
            duration=30,
            speaker='Speaker',
            theme=f'video={with_video} thumb={with_thumbnail}',
            contents='contents',
            status='approved',
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

    def test_thumbnail_appears_before_youtube_embed(self):
        """動画とサムネイルが両方ある時、サムネイルが動画より上に出る."""
        detail = self._create_detail(with_video=True, with_thumbnail=True)

        html = self._get_html(detail)

        thumbnail_pos = html.index('event-detail-thumbnail img-fluid')
        video_pos = html.index('https://www.youtube.com/embed/')
        self.assertLess(thumbnail_pos, video_pos)

    def test_video_only_page_renders_embed_without_thumbnail(self):
        """サムネイルが無くても動画は表示され、空の画像枠は出ない."""
        detail = self._create_detail(with_video=True, with_thumbnail=False)

        html = self._get_html(detail)

        self.assertIn('https://www.youtube.com/embed/', html)
        self.assertNotIn('event-detail-thumbnail img-fluid', html)

    def test_thumbnail_only_page_renders_image_without_embed(self):
        """動画が無くてもサムネイルは表示され、空の動画枠は出ない."""
        detail = self._create_detail(with_video=False, with_thumbnail=True)

        html = self._get_html(detail)

        self.assertIn('event-detail-thumbnail img-fluid', html)
        self.assertNotIn('https://www.youtube.com/embed/', html)
