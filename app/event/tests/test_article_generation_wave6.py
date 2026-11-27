"""Wave 6-A1 tests (#986): 24h transcript deferral, API error separation, scan window starvation."""
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from community.models import Community
from event.models import Event, EventDetail
from event.services import article_generation, youtube_service


class ArticleGenerationWave6Tests(TestCase):
    def setUp(self):
        self.community = Community.objects.create(
            name="Wave6集会", start_time="21:00", duration=60, weekdays=["Sat"],
            frequency="毎週", organizers="org", recording_allowed=True,
        )
        self.event = Event.objects.create(
            community=self.community, date=timezone.localdate(), start_time="21:00", duration=60, weekday="Sat",
        )
        self.detail = EventDetail.objects.create(
            event=self.event, detail_type="LT", speaker="Spk", theme="Wave6",
            start_time="21:00", duration=20, status="approved",
            youtube_url="https://www.youtube.com/watch?v=VidWave6001",
        )

    def test_c11_max_deferrals_and_retry_cap_span_24h(self):
        self.assertGreaterEqual(article_generation.MAX_DEFERRALS, 8)
        self.assertGreaterEqual(article_generation.CLAIM_SCAN_LIMIT, 50)
        # Retry delay is capped at MAX_RETRY_DELAY (4h)
        now = timezone.now()
        next_at = article_generation._retry_at(8)
        self.assertLessEqual(next_at - now, timedelta(hours=4, seconds=5))

    def test_c11_transcript_api_error_separated_from_absent(self):
        with mock.patch.object(youtube_service, "build", side_effect=RuntimeError("API 503")):
            # Default raise_on_error=False preserves legacy None fallback
            self.assertIsNone(youtube_service.get_transcript("VidWave6001"))
            # raise_on_error=True raises TranscriptFetchError for retryable handling
            with self.assertRaises(youtube_service.TranscriptFetchError):
                youtube_service.get_transcript("VidWave6001", raise_on_error=True)

    def test_c11_generate_converts_transcript_api_error_to_retryable_error(self):
        with mock.patch.object(
            article_generation, "fetch_transcript",
            side_effect=youtube_service.TranscriptFetchError("TimeoutError"),
        ):
            with self.assertRaises(article_generation.ArticleGenerationError) as ctx:
                article_generation._generate(self.detail)
            self.assertIn("transcript_api_error", ctx.exception.reason)
