from datetime import date, time

from django.test import TestCase

from community.models import Community
from event.models import Event, RecurrenceRule
from event.recurrence_labels import get_community_recurrence_label


class RecurrenceLabelTest(TestCase):
    def test_weekly_label_uses_rule_and_master_event_time(self):
        community = Community.objects.create(
            name='週次集会',
            frequency='旧開催周期',
            start_time=time(21, 0),
            duration=60,
            weekdays=['Tue'],
            organizers='主催者',
            status='approved',
        )
        rule = RecurrenceRule.objects.create(
            community=community,
            frequency='WEEKLY',
            interval=1,
            start_date=date(2026, 5, 5),
        )
        Event.objects.create(
            community=community,
            date=date(2026, 5, 5),
            start_time=time(22, 0),
            duration=60,
            weekday='Tue',
            recurrence_rule=rule,
            is_recurring_master=True,
        )

        self.assertEqual(get_community_recurrence_label(community), '毎週火曜日 22:00-23:00')

    def test_other_monthly_date_label_hides_internal_choice_name(self):
        community = Community.objects.create(
            name='UIUXデザイン集会相当',
            frequency='カスタムルール',
            start_time=time(22, 0),
            duration=80,
            weekdays=['Other'],
            organizers='主催者',
            status='approved',
        )
        RecurrenceRule.objects.create(
            community=community,
            frequency='OTHER',
            interval=1,
            custom_rule='毎月11日',
            start_date=date(2026, 5, 11),
        )

        self.assertEqual(get_community_recurrence_label(community), '毎月11日 22:00-23:20')

    def test_unconfigured_label_does_not_use_community_frequency(self):
        community = Community.objects.create(
            name='未設定集会',
            frequency='毎週',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Tue'],
            organizers='主催者',
            status='approved',
        )

        self.assertEqual(get_community_recurrence_label(community), '定期開催未設定')
