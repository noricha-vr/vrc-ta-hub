from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from django.core.cache import cache
from django.db.utils import OperationalError
from django.test import TestCase
from django.urls import reverse

from vket.models import VketCollaboration

TOKYO = ZoneInfo('Asia/Tokyo')
# 会期外はヒーローに合わせた固定画像（作り替え前は R2 の写真）
DEFAULT_IMAGE = 'http://testserver/static/ta_hub/og/top.jpg'
TOP_IMAGE = 'vket/og/vket-2026-winter/top.jpg'


class TopOgImageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.collaboration = VketCollaboration.objects.create(
            slug='winter', name='冬の技術学術WEEK', phase='locked',
            period_start=date(2026, 12, 5), period_end=date(2026, 12, 20),
            registration_deadline=date(2026, 11, 1), lt_deadline=date(2026, 11, 20),
            settings_json={'og_images': {'top': TOP_IMAGE}},
        )

    def og_images(self, now):
        with patch('ta_hub.views.timezone.now', return_value=now):
            response = self.client.get(reverse('ta_hub:index'))
        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, 'html.parser')
        return (
            [tag['content'] for tag in soup.find_all('meta', property='og:image')],
            [tag['content'] for tag in soup.find_all('meta', attrs={'name': 'twitter:image'})],
        )

    def test_week_image_only_during_period_in_tokyo(self):
        for now, expected in [
            (datetime(2026, 12, 4, 23, 59, tzinfo=TOKYO), DEFAULT_IMAGE),
            (datetime(2026, 12, 5, 0, 0, tzinfo=TOKYO), TOP_IMAGE),
            (datetime(2026, 12, 20, 23, 59, tzinfo=TOKYO), TOP_IMAGE),
            (datetime(2026, 12, 21, 0, 0, tzinfo=TOKYO), DEFAULT_IMAGE),
        ]:
            with self.subTest(now=now):
                og, twitter = self.og_images(now)
                self.assertEqual(len(og), 1)
                self.assertEqual(og, twitter)
                self.assertTrue(og[0].endswith(expected), og[0])
                self.assertTrue(og[0].startswith('http'), og[0])

    def test_missing_or_unsafe_setting_keeps_default(self):
        now = datetime(2026, 12, 10, 12, tzinfo=TOKYO)
        for images in [
            {}, {'top': ''}, {'top': 1}, {'top': 'https://example.com/x.jpg'}, {'top': '//evil.example/x.jpg'},
            {'top': '../secret.jpg'}, {'top': 'vket\\x.jpg'}, {'top': 'vket/x.jpg?v=1'}, {'top': 'vket/\nx.jpg'},
        ]:
            with self.subTest(images=images):
                self.collaboration.settings_json = {'og_images': images}
                self.collaboration.save(update_fields=['settings_json'])
                og, _ = self.og_images(now)
                self.assertEqual(og, [DEFAULT_IMAGE])

    def test_draft_collaboration_is_ignored(self):
        self.collaboration.phase = VketCollaboration.Phase.DRAFT
        self.collaboration.save(update_fields=['phase'])
        og, _ = self.og_images(datetime(2026, 12, 10, 12, tzinfo=TOKYO))
        self.assertEqual(og, [DEFAULT_IMAGE])

    def test_database_error_keeps_default_image(self):
        with patch('ta_hub.views.top_page_og_image_url', side_effect=OperationalError('down')):
            og, twitter = self.og_images(datetime(2026, 12, 10, 12, tzinfo=TOKYO))
        self.assertEqual(og, [DEFAULT_IMAGE])
        self.assertEqual(twitter, [DEFAULT_IMAGE])
