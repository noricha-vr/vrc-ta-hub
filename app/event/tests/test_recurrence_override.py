"""定期イベントのユーザー例外を保持するテスト。"""

from datetime import time, timedelta

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from community.models import Community
from event.models import Event, EventOccurrenceTombstone, RecurrenceRule
from event.recurrence.persistence import create_recurring_events
from event.services.recurrence_override import exclude_tombstoned_dates


class EventOccurrenceTombstoneTests(TestCase):
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

    def test_master_skips_existing_event_date(self):
        """先頭候補が既存イベント日ならマスターは次の空き日に作る（unique制約衝突の回避）"""
        first_date = timezone.localdate() + timedelta(days=7)
        second_date = first_date + timedelta(days=7)
        third_date = second_date + timedelta(days=7)
        rule = RecurrenceRule.objects.create(
            community=self.community,
            frequency='WEEKLY',
            interval=1,
            start_date=first_date,
        )
        # 初回だけ tombstone、2回目はDBに残っている状態
        EventOccurrenceTombstone.objects.create(
            community=self.community,
            date=first_date,
            original_start_time=time(21, 0),
            reason=EventOccurrenceTombstone.Reason.DELETED,
        )
        Event.objects.create(
            community=self.community,
            date=second_date,
            start_time=time(21, 0),
            duration=60,
            weekday=second_date.strftime('%a').upper()[:3],
        )

        created = create_recurring_events(
            community=self.community,
            rule=rule,
            dates=[first_date, second_date, third_date],
            start_time=time(21, 0),
            duration=60,
        )

        self.assertEqual([event.date for event in created], [third_date])
        self.assertTrue(created[0].is_recurring_master)

    def test_returns_empty_when_all_dates_occupied(self):
        """全候補日が埋まっていればイベントを1件も作らない（呼び出し側がロールバックできる）"""
        first_date = timezone.localdate() + timedelta(days=7)
        rule = RecurrenceRule.objects.create(
            community=self.community,
            frequency='WEEKLY',
            interval=1,
            start_date=first_date,
        )
        Event.objects.create(
            community=self.community,
            date=first_date,
            start_time=time(21, 0),
            duration=60,
            weekday=first_date.strftime('%a').upper()[:3],
        )

        created = create_recurring_events(
            community=self.community,
            rule=rule,
            dates=[first_date],
            start_time=time(21, 0),
            duration=60,
        )

        self.assertEqual(created, [])
        self.assertFalse(
            Event.objects.filter(
                community=self.community,
                is_recurring_master=True,
            ).exists()
        )
