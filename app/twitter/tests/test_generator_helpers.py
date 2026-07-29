"""投稿文生成ヘルパーのテスト。"""

import datetime
from unittest.mock import patch

from django.test import TestCase, override_settings, tag

from community.models import Community
from event.models import Event, EventDetail
from twitter.models import TweetQueue

@tag('offline_external_api')
class GetGeneratorHelperTest(TestCase):
    """get_generator ヘルパー関数のテスト"""

    def test_new_community_returns_callable(self):
        """new_community タイプで callable が返る"""
        from twitter.tweet_generator import get_generator
        generator = get_generator("new_community")
        self.assertIsNotNone(generator)
        self.assertTrue(callable(generator))

    def test_lt_returns_callable(self):
        """lt タイプで callable が返る"""
        from twitter.tweet_generator import get_generator
        generator = get_generator("lt")
        self.assertIsNotNone(generator)
        self.assertTrue(callable(generator))

    def test_special_returns_callable(self):
        """special タイプで callable が返る"""
        from twitter.tweet_generator import get_generator
        generator = get_generator("special")
        self.assertIsNotNone(generator)
        self.assertTrue(callable(generator))

    def test_unknown_type_returns_none(self):
        """未知の tweet_type で None が返る"""
        from twitter.tweet_generator import get_generator
        generator = get_generator("unknown")
        self.assertIsNone(generator)

    def test_empty_string_returns_none(self):
        """空文字列で None が返る"""
        from twitter.tweet_generator import get_generator
        generator = get_generator("")
        self.assertIsNone(generator)


@tag('offline_external_api')
class GetPosterImageUrlHelperTest(TestCase):
    """get_poster_image_url ヘルパー関数のテスト"""

    def setUp(self):
        with patch("twitter.services.tweet_generation.threading.Thread"):
            self.community = Community.objects.create(
                name="Poster Test Community",
                start_time=datetime.time(22, 0),
                duration=60,
                weekdays=["Mon"],
                frequency="毎週",
                organizers="Test",
                description="テスト用",
                platform="All",
                status="approved",
            )

    def test_no_poster_returns_empty_string(self):
        """ポスター画像がない場合は空文字列を返す"""
        from twitter.tweet_generator import get_poster_image_url
        result = get_poster_image_url(self.community)
        self.assertEqual(result, "")

    @override_settings(AWS_S3_CUSTOM_DOMAIN='data.vrc-ta-hub.com')
    def test_with_custom_domain_returns_cf_resized_url(self):
        """AWS_S3_CUSTOM_DOMAIN 設定時は CF Image Resizing URL を返す"""
        Community.objects.filter(pk=self.community.pk).update(
            poster_image="community/1/poster.webp",
        )
        self.community.refresh_from_db()

        from twitter.tweet_generator import get_poster_image_url
        result = get_poster_image_url(self.community)
        self.assertEqual(
            result,
            "https://data.vrc-ta-hub.com/cdn-cgi/image/width=960,quality=80,format=auto/community/1/poster.webp",
        )

    @override_settings(AWS_S3_CUSTOM_DOMAIN='')
    def test_without_custom_domain_falls_back_to_url(self):
        """AWS_S3_CUSTOM_DOMAIN が未設定の場合は poster.url にフォールバック"""
        Community.objects.filter(pk=self.community.pk).update(
            poster_image="community/1/poster.webp",
        )
        self.community.refresh_from_db()

        from twitter.tweet_generator import get_poster_image_url
        result = get_poster_image_url(self.community)
        # FileField に url 属性があるので何かしらの値が返る
        self.assertNotEqual(result, "")

    @override_settings(AWS_S3_CUSTOM_DOMAIN='data.vrc-ta-hub.com')
    def test_tweet_image_prefers_event_detail_thumbnail(self):
        """EventDetailサムネイルがある投稿では集会ポスターより優先する."""
        Community.objects.filter(pk=self.community.pk).update(
            poster_image="community/1/poster.webp",
        )
        self.community.refresh_from_db()
        event = Event.objects.create(
            community=self.community,
            date=datetime.date(2099, 1, 1),
            start_time=datetime.time(22, 0),
            duration=60,
        )
        detail = EventDetail.objects.create(
            event=event,
            detail_type="LT",
            status="pending",
            speaker="テスト太郎",
            theme="スライドの話",
            thumbnail_image="thumbnail/slide-first.jpg",
        )
        queue_item = TweetQueue.objects.create(
            tweet_type="lt",
            community=self.community,
            event=event,
            event_detail=detail,
            status="ready",
        )

        from twitter.tweet_generator import get_tweet_image_url
        result = get_tweet_image_url(queue_item)

        self.assertIn("/cdn-cgi/image/width=960", result)
        self.assertIn("thumbnail/slide-first.jpg", result)
        self.assertNotIn("community/1/poster.webp", result)

    @override_settings(AWS_S3_CUSTOM_DOMAIN='data.vrc-ta-hub.com')
    def test_tweet_image_falls_back_to_poster_without_thumbnail(self):
        """EventDetailサムネイルがない投稿では集会ポスターを使う."""
        Community.objects.filter(pk=self.community.pk).update(
            poster_image="community/1/poster.webp",
        )
        self.community.refresh_from_db()
        event = Event.objects.create(
            community=self.community,
            date=datetime.date(2099, 1, 1),
            start_time=datetime.time(22, 0),
            duration=60,
        )
        detail = EventDetail.objects.create(
            event=event,
            detail_type="LT",
            status="pending",
            speaker="テスト太郎",
            theme="スライドの話",
        )
        queue_item = TweetQueue.objects.create(
            tweet_type="lt",
            community=self.community,
            event=event,
            event_detail=detail,
            status="ready",
        )

        from twitter.tweet_generator import get_tweet_image_url
        result = get_tweet_image_url(queue_item)

        self.assertIn("/cdn-cgi/image/width=960", result)
        self.assertIn("community/1/poster.webp", result)
