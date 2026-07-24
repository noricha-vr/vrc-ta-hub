"""定期イベントのユーザー例外を保持するテスト。"""

from datetime import time, timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from community.models import Community
from event.models import Event, EventOccurrenceTombstone, RecurrenceRule
from event.recurrence.persistence import create_recurring_events
from event.services.recurrence_override import exclude_tombstoned_dates
from event.tests.tweet_generation import TweetGenerationPatchMixin


class EventOccurrenceTombstoneTests(TweetGenerationPatchMixin, TestCase):
    def setUp(self):
        self.community = Community.objects.create(
            name='Tombstone集会',
            status='approved',
            start_time=time(21, 0),
            duration=60,
        )

    def test_community_and_date_are_unique(self):
        target_date = timezone.localdate() + timedelta(days=7)
        EventOccurrenceTombstone.objects.create(
            community=self.community,
            date=target_date,
            original_start_time=time(21, 0),
            reason=EventOccurrenceTombstone.Reason.DELETED,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            EventOccurrenceTombstone.objects.create(
                community=self.community,
                date=target_date,
                original_start_time=time(22, 0),
                reason=EventOccurrenceTombstone.Reason.RESCHEDULED,
            )

    def test_exclude_tombstoned_dates_preserves_other_dates(self):
        first_date = timezone.localdate() + timedelta(days=7)
        second_date = first_date + timedelta(days=7)
        EventOccurrenceTombstone.objects.create(
            community=self.community,
            date=first_date,
            original_start_time=time(21, 0),
            reason=EventOccurrenceTombstone.Reason.DELETED,
        )

        self.assertEqual(
            exclude_tombstoned_dates(
                self.community,
                [first_date, second_date],
            ),
            [second_date],
        )

    def test_persistence_skips_tombstoned_occurrence(self):
        first_date = timezone.localdate() + timedelta(days=7)
        second_date = first_date + timedelta(days=7)
        rule = RecurrenceRule.objects.create(
            community=self.community,
            frequency='WEEKLY',
            interval=1,
            start_date=first_date,
        )
        EventOccurrenceTombstone.objects.create(
            community=self.community,
            date=first_date,
            original_start_time=time(21, 0),
            reason=EventOccurrenceTombstone.Reason.DELETED,
        )

        created = create_recurring_events(
            community=self.community,
            rule=rule,
            dates=[first_date, second_date],
            start_time=time(21, 0),
            duration=60,
        )

        self.assertEqual([event.date for event in created], [second_date])
        self.assertTrue(created[0].is_recurring_master)
        self.assertFalse(
            Event.objects.filter(
                community=self.community,
                date=first_date,
            ).exists()
        )
