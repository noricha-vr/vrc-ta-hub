from datetime import time
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from community.models import Community
from event.models import Event, RecurrenceRule


class MigrateCommunityFrequencyToRecurrenceCommandTest(TestCase):
    def test_migrates_weekly_frequency_to_rule_and_master_event(self):
        community = Community.objects.create(
            name='毎週集会',
            frequency='毎週',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Tue'],
            organizers='主催者',
            status='approved',
        )

        call_command(
            'migrate_community_frequency_to_recurrence',
            community_id=community.pk,
            base_date='2026-05-05',
            stdout=StringIO(),
        )

        rule = RecurrenceRule.objects.get(community=community)
        self.assertEqual(rule.frequency, 'WEEKLY')
        self.assertEqual(rule.interval, 1)
        self.assertEqual(rule.start_date.isoformat(), '2026-05-05')

        master = Event.objects.get(community=community, is_recurring_master=True)
        self.assertEqual(master.recurrence_rule, rule)
        self.assertEqual(master.date.isoformat(), '2026-05-05')

    def test_migrates_monthly_date_to_custom_other_rule(self):
        community = Community.objects.create(
            name='毎月11日集会',
            frequency='毎月11日',
            start_time=time(22, 0),
            duration=80,
            weekdays=['Other'],
            organizers='主催者',
            status='approved',
        )

        call_command(
            'migrate_community_frequency_to_recurrence',
            community_id=community.pk,
            base_date='2026-05-05',
            stdout=StringIO(),
        )

        rule = RecurrenceRule.objects.get(community=community)
        self.assertEqual(rule.frequency, 'OTHER')
        self.assertEqual(rule.custom_rule, '毎月11日')
        self.assertEqual(rule.start_date.isoformat(), '2026-05-11')

    def test_skips_unparseable_frequency(self):
        community = Community.objects.create(
            name='不明集会',
            frequency='気が向いたら',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Tue'],
            organizers='主催者',
            status='approved',
        )

        call_command(
            'migrate_community_frequency_to_recurrence',
            community_id=community.pk,
            base_date='2026-05-05',
            stdout=StringIO(),
        )

        self.assertFalse(RecurrenceRule.objects.filter(community=community).exists())
