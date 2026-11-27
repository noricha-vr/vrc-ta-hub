"""Wave 6-A2 tests (#987): H-02 (publish_sync youtube_url & article_status), H-12 (Discord retry & no-email notify + claim release)."""
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from community.models import Community
from event.models import Event, EventDetail, RecordingReviewItem
from event.notifications import _send_video_article_notice, notify_applicant_of_article_published
from event.services.article_generation import _notify_first_time
from event.services.recording_ops.article_status import resolve_machine_article_status
from event.services.recording_ops.publish_sync import mark_recording_item_published


class PublishSyncAndNotificationsWave6Tests(TestCase):
    def setUp(self):
        self.community = Community.objects.create(
            name="Wave6A2集会", start_time="21:00", duration=60, weekdays=["Sat"],
            frequency="毎週", organizers="org", recording_allowed=True,
        )
        self.event = Event.objects.create(
            community=self.community, date=timezone.localdate(), start_time="21:00", duration=60, weekday="Sat",
        )
        self.detail = EventDetail.objects.create(
            event=self.event, detail_type="LT", speaker="Spk", theme="Wave6A2",
            start_time="21:00", duration=20, status="approved", recording_policy="public",
        )
        self.item = RecordingReviewItem.objects.create(
            event_detail=self.detail,
            edl_revision=1,
            worker_synced_revision=1,
            review_status=RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED,
            youtube_draft_video_id="PubVid12345",
            youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.UNLISTED,
        )

    def test_h02_mark_published_populates_missing_youtube_url(self):
        self.assertFalse(self.detail.youtube_url)
        res = mark_recording_item_published(self.detail.pk, video_id="PubVid12345")
        self.assertTrue(res["synced"])
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.youtube_url, "https://www.youtube.com/watch?v=PubVid12345")

    def test_h02_article_status_reports_url_missing_when_published_without_url(self):
        self.item.review_status = RecordingReviewItem.ReviewStatus.PUBLISHED
        self.item.youtube_draft_privacy = RecordingReviewItem.YouTubePrivacy.PUBLIC
        self.item.save()
        status = resolve_machine_article_status(self.detail)
        self.assertEqual(status["state"], "url_missing")

    def test_h12_video_article_notice_retries_transient_failure(self):
        calls = []

        def flaky(_detail):
            calls.append(1)
            if len(calls) < 2:
                raise RuntimeError("transient 503")
            return True

        with mock.patch("event.notifications.notify_video_article_published", side_effect=flaky), \
                mock.patch("time.sleep", return_value=None):
            ok = _send_video_article_notice(self.detail)
        self.assertTrue(ok)
        self.assertEqual(len(calls), 2)

    def test_h12_notify_published_sends_discord_without_email_and_releases_claim_on_error(self):
        self.detail.h1 = "Generated Title"
        self.detail.contents = "Generated Body"
        self.detail.article_generated_at = timezone.now()
        self.detail.save()

        # Without email, approved detail still triggers Discord notification
        with mock.patch("event.notifications.get_published_article_for_notification", return_value=self.detail), \
                mock.patch("event.notifications.article_discord_replaced_by_video_notice", return_value=False), \
                mock.patch("event.notifications.send_discord_notification_for_article") as mock_dc:
            ok = notify_applicant_of_article_published(self.detail, None)
        self.assertTrue(ok)
        mock_dc.assert_called_once()

        # Exception after claiming releases article_published_notified_at
        with mock.patch("event.services.article_generation.get_material_reminder_recipient", return_value=None), \
                mock.patch("event.services.article_generation._send_published_notification", side_effect=RuntimeError("boom")):
            _notify_first_time(self.detail.pk)
        self.detail.refresh_from_db()
        self.assertIsNone(self.detail.article_published_notified_at)
