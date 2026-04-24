from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.db.utils import OperationalError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from community.models import Community
from event.models import Event, EventDetail
from ta_hub.index_cache import get_index_view_cache_key
from ta_hub.views import VKET_ACHIEVEMENTS


def _expected_vket_achievements_no_images():
    """DB障害時の vket_achievements: image=None の2件リスト"""
    return [dict(a, image=None) for a in VKET_ACHIEVEMENTS]


class IndexViewDegradedModeTest(TestCase):
    def setUp(self):
        self.client = Client()
        cache.clear()

    def tearDown(self):
        cache.clear()

    @patch("ta_hub.views.Event.objects.filter")
    @patch("ta_hub.views.Post.objects.filter")
    def test_index_view_returns_static_page_when_database_is_unavailable(
        self, mock_post_filter, mock_event_filter
    ):
        # Post.objects.filter は _build_vket_achievements(with_images=False) では呼ばれない
        # Event.objects.filter が OperationalError を送出することでDB障害をシミュレート
        mock_event_filter.side_effect = OperationalError("db unavailable")

        response = self.client.get(reverse("ta_hub:index"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["database_degraded"])
        # DB障害時は画像なしの vket_achievements が返る
        self.assertEqual(
            response.context["vket_achievements"],
            _expected_vket_achievements_no_images(),
        )
        self.assertEqual(response.context["upcoming_events"], [])
        self.assertEqual(response.context["upcoming_event_details"], [])
        self.assertEqual(response.context["special_events"], [])
        self.assertContains(response, "Googleカレンダーと連携して予定を管理")

    @patch("ta_hub.views.Post.objects.filter")
    def test_index_view_uses_cached_payload_when_database_is_unavailable(self, mock_post_filter):
        # キャッシュには vket_achievements を含めない（request依存のためキャッシュ対象外）
        cache.set(
            "index_view_data_2026-04-04",
            {
                "upcoming_events": [],
                "upcoming_event_details": [],
                "special_events": [],
            },
            60,
        )
        # _build_vket_achievements(with_images=True) の Post.objects.filter(...).only(...) をモック
        mock_post_filter.return_value.only.return_value = []

        with patch("ta_hub.views.get_vrchat_today", return_value="2026-04-04"):
            response = self.client.get(reverse("ta_hub:index"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["database_degraded"])
        # キャッシュヒット時も vket_achievements はキャッシュ外で毎回生成される（image=None）
        self.assertEqual(
            response.context["vket_achievements"],
            _expected_vket_achievements_no_images(),
        )
        self.assertEqual(response.context["upcoming_events"], [])
        self.assertEqual(response.context["upcoming_event_details"], [])
        self.assertEqual(response.context["special_events"], [])
        mock_post_filter.assert_called_once()


class IndexViewEventDetailCacheInvalidationTest(TestCase):
    def setUp(self):
        cache.clear()
        self.cache_key = get_index_view_cache_key()
        self.community = Community.objects.create(
            name='キャッシュ検証集会',
            status='approved',
            frequency='毎週',
        )
        self.event = Event.objects.create(
            community=self.community,
            date=timezone.localdate() - timedelta(days=1),
            start_time='22:00',
            duration=60,
            weekday='Mon',
        )

    def tearDown(self):
        cache.clear()

    def _set_stale_index_cache(self):
        cache.set(self.cache_key, {'upcoming_event_details': ['stale']}, 60)
        self.assertIsNotNone(cache.get(self.cache_key))

    def test_lt_creation_clears_index_cache(self):
        self._set_stale_index_cache()

        with self.captureOnCommitCallbacks(execute=True):
            EventDetail.objects.create(
                event=self.event,
                detail_type='LT',
                speaker='新規登壇者',
                theme='新規LT',
                status='approved',
            )

        self.assertIsNone(cache.get(self.cache_key))

    def test_lt_update_clears_index_cache(self):
        detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            speaker='更新前',
            theme='更新前LT',
            status='approved',
        )
        self._set_stale_index_cache()

        detail.theme = '更新後LT'
        with self.captureOnCommitCallbacks(execute=True):
            detail.save(update_fields=['theme'])

        self.assertIsNone(cache.get(self.cache_key))

    def test_lt_delete_clears_index_cache(self):
        detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            speaker='削除対象',
            theme='削除対象LT',
            status='approved',
        )
        self._set_stale_index_cache()

        with self.captureOnCommitCallbacks(execute=True):
            detail.delete()

        self.assertIsNone(cache.get(self.cache_key))

    def test_blog_update_keeps_index_cache(self):
        detail = EventDetail.objects.create(
            event=self.event,
            detail_type='BLOG',
            speaker='ブログ登壇者',
            theme='ブログ記事',
            status='approved',
        )
        self._set_stale_index_cache()

        detail.theme = 'ブログ記事更新'
        with self.captureOnCommitCallbacks(execute=True):
            detail.save(update_fields=['theme'])

        self.assertIsNotNone(cache.get(self.cache_key))
