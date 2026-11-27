"""Wave 4-B tests (#985): C-03 (STOP_PUBLISH lease expiry), C-04 (late apply_edl pending_stop), H-12 (cancel revision sync)."""
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from community.models import Community
from event.models import Event, EventDetail, RecordingEditOrder, RecordingReviewItem
from event.services.recording_ops.order_actions import cancel_edit_order
from event.services.recording_ops.publish_sync import mark_recording_item_published
from event.services.recording_ops.stop_queue import record_partial_or_late_apply_edl_output
from event.services.recording_ops.worker import _expire_stale_running_orders

User = get_user_model()


class RecordingOpsWave4Tests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(user_name="staff_w4", email="s@example.com", password="pw", is_staff=True)
        self.community = Community.objects.create(
            name="Wave4集会", start_time="21:00", duration=60, weekdays=["Sat"],
            frequency="毎週", organizers="org", recording_allowed=True,
        )
        self.event = Event.objects.create(
            community=self.community, date=timezone.localdate(), start_time="21:00", duration=60, weekday="Sat",
        )
        self.detail = EventDetail.objects.create(
            event=self.event, detail_type="LT", speaker="Spk", theme="Wave4",
            start_time="21:00", duration=20, status="approved", recording_policy="public",
        )
        self.item = RecordingReviewItem.objects.create(
            event_detail=self.detail,
            edl_revision=2,
            worker_synced_revision=2,
            review_status=RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED,
            youtube_draft_video_id="OldVid12345",
            youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.UNLISTED,
        )

    def test_c03_expired_stop_publish_re_enqueues_compensating_stop(self):
        now = timezone.now()
        old_stop = RecordingEditOrder.objects.create(
            review_item=self.item,
            event_detail=self.detail,
            order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
            status=RecordingEditOrder.Status.RUNNING,
            base_revision=2,
            target_revision=2,
            started_at=now - timedelta(hours=1),
            requested_by=self.staff,
            active_lock_key=f"item:{self.item.pk}",
        )
        expired = _expire_stale_running_orders(now=now)
        self.assertEqual(expired, 1)
        old_stop.refresh_from_db()
        self.assertEqual(old_stop.status, RecordingEditOrder.Status.FAILED)
        # Compensating STOP_PUBLISH must be enqueued so revoked/stopped video is not left public
        new_stops = RecordingEditOrder.objects.filter(
            review_item=self.item,
            order_type=RecordingEditOrder.OrderType.STOP_PUBLISH,
            status=RecordingEditOrder.Status.QUEUED,
        )
        self.assertEqual(new_stops.count(), 1)

    def test_c04_late_apply_edl_with_valid_consent_does_not_pollute_pending_stop(self):
        order = RecordingEditOrder.objects.create(
            review_item=self.item,
            event_detail=self.detail,
            order_type=RecordingEditOrder.OrderType.APPLY_EDL,
            status=RecordingEditOrder.Status.FAILED,
            base_revision=2,
            target_revision=2,
            requested_by=self.staff,
        )
        record_partial_or_late_apply_edl_output(
            detail=self.detail,
            item=self.item,
            order=order,
            reported_video_id="NewVid12345",
            youtube_draft_privacy=RecordingReviewItem.YouTubePrivacy.UNLISTED,
            review_status=RecordingReviewItem.ReviewStatus.UNLISTED_SCHEDULED,
            consent_valid=True,
            now=timezone.now(),
        )
        self.item.refresh_from_db()
        self.assertEqual(self.item.youtube_draft_video_id, "NewVid12345")
        self.assertEqual(self.item.pending_stop_video_ids, [])
        # Publish sync must NOT fail with 409 pending_stop_exists
        res = mark_recording_item_published(self.detail.pk, video_id="NewVid12345")
        self.assertTrue(res["synced"])

    def test_h12_cancel_order_keeps_worker_synced_revision_aligned(self):
        order = RecordingEditOrder.objects.create(
            review_item=self.item,
            event_detail=self.detail,
            order_type=RecordingEditOrder.OrderType.APPLY_EDL,
            status=RecordingEditOrder.Status.QUEUED,
            base_revision=2,
            target_revision=2,
            requested_by=self.staff,
            active_lock_key=f"item:{self.item.pk}",
        )
        cancel_edit_order(self.detail.pk, order.pk, expected_revision=2, requested_by=self.staff)
        self.item.refresh_from_db()
        self.assertEqual(self.item.edl_revision, 3)
        self.assertEqual(self.item.worker_synced_revision, 3)
