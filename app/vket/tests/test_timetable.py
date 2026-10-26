from datetime import date, time, timedelta
from unittest.mock import patch

from django.test import TestCase

from tests.factories import make_community, make_event, make_event_detail
from vket.models import VketCollaboration, VketParticipation
from vket.timetable import build_collaboration_timetable


class CollaborationTimetableTests(TestCase):
    def setUp(self):
        self.collaboration = VketCollaboration.objects.create(
            slug='timetable', name='冬のコラボ', phase='locked',
            period_start=date(2026, 12, 5), period_end=date(2026, 12, 20),
            registration_deadline=date(2026, 11, 1), lt_deadline=date(2026, 11, 20),
        )
        self.sequence = 0

    def add_slot(self, day=date(2026, 12, 5), start=time(21), duration=60, **details):
        self.sequence += 1
        community = make_community(name=f'集会{self.sequence}')
        event = make_event(community, event_date=day, start_time=start, duration=duration)
        participation = VketParticipation.objects.create(
            collaboration=self.collaboration, community=community, published_event=event,
        )
        if details:
            # factory の既定値を空にして、指定した項目だけで判定させる
            make_event_detail(event, **{'speaker': '', 'theme': '', **details})
        return participation, event

    def build(self, today=date(2026, 12, 5)):
        return build_collaboration_timetable(self.collaboration, today)

    def first_day_blocks(self, timetable):
        return timetable['weeks'][0]['days'][5]['blocks']

    def test_monday_weeks_and_partial_week_labels(self):
        timetable = self.build()
        weeks = timetable['weeks']
        self.assertEqual(len(weeks), 3)
        self.assertEqual(weeks[0]['label'], '第1週 12/5〜12/6')
        self.assertEqual(weeks[1]['label'], '第2週 12/7〜12/13')
        self.assertEqual(weeks[2]['label'], '第3週 12/14〜12/20')
        self.assertEqual(weeks[0]['days'][0]['date'], date(2026, 11, 30))
        for week in weeks:
            self.assertEqual(len(week['days']), 7)
            self.assertEqual(week['days'][0]['date'].weekday(), 0)
        self.assertFalse(weeks[0]['days'][4]['in_period'])
        self.assertTrue(weeks[0]['days'][5]['in_period'])

    def test_initial_week_and_today_highlight(self):
        for today, active in [(date(2026, 11, 1), 0), (date(2026, 12, 9), 1), (date(2027, 1, 1), 2)]:
            with self.subTest(today=today):
                weeks = self.build(today)['weeks']
                self.assertEqual([i for i, w in enumerate(weeks) if w['active']], [active])
                marked = [d['date'] for w in weeks for d in w['days'] if d['is_today']]
                self.assertEqual(marked, [today] if self.collaboration.period_start <= today <= self.collaboration.period_end else [])

    @patch('utils.vrchat_time.timezone.now')
    def test_default_today_uses_four_am_boundary(self, now):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        now.return_value = datetime(2026, 12, 7, 3, 59, tzinfo=ZoneInfo('Asia/Tokyo'))
        timetable = build_collaboration_timetable(self.collaboration)
        self.assertTrue(timetable['weeks'][0]['active'])
        self.assertTrue(timetable['weeks'][0]['days'][6]['is_today'])

    def test_overlapping_slots_use_lanes_and_touching_slots_reuse_full_width(self):
        self.add_slot(start=time(21), duration=60)
        self.add_slot(start=time(21, 30), duration=60)
        self.add_slot(start=time(21, 45), duration=15)
        self.add_slot(start=time(22, 30), duration=30)
        blocks = self.first_day_blocks(self.build())
        self.assertEqual([b['lane'] for b in blocks], [0, 1, 2, 0])
        self.assertEqual([b['lane_count'] for b in blocks], [3, 3, 3, 1])
        self.assertIn('width:100.0000%', blocks[-1]['style'])

    def test_time_range_uses_all_weeks_and_rounds_to_hours(self):
        self.add_slot(start=time(19, 40), duration=35)
        self.add_slot(day=date(2026, 12, 19), start=time(22, 15), duration=60)
        timetable = self.build()
        self.assertEqual(timetable['start_minute'], 19 * 60)
        self.assertEqual(timetable['end_minute'], 24 * 60)
        self.assertEqual(timetable['ticks'][0]['label'], '19:00')
        self.assertEqual(timetable['ticks'][-1]['label'], '24:00')
        self.assertEqual(timetable['height'], 600)

    def test_midnight_slot_is_clipped_to_start_day(self):
        self.add_slot(start=time(23, 30), duration=120)
        timetable = self.build()
        block = self.first_day_blocks(timetable)[0]
        self.assertEqual(block['end'], 24 * 60)
        self.assertEqual(block['time_label'], '23:30〜01:30')
        self.assertIn('height:50.0000%', block['style'])
        self.assertEqual(timetable['weeks'][0]['days'][6]['blocks'], [])

    def test_only_approved_lt_with_speaker_or_theme_are_confirmed(self):
        for detail, confirmed in [
            ({'detail_type': 'LT', 'status': 'approved', 'theme': '確定タイトル'}, True),
            ({'detail_type': 'LT', 'status': 'approved', 'speaker': '発表者'}, True),
            ({'detail_type': 'LT', 'status': 'approved'}, False),
            ({'detail_type': 'LT', 'status': 'pending', 'theme': '審査中'}, False),
            ({'detail_type': 'SPECIAL', 'status': 'approved', 'theme': '特別企画'}, False),
        ]:
            self.add_slot(**detail)
        blocks = self.first_day_blocks(self.build())
        self.assertEqual([b['has_lt'] for b in blocks], [True, True, False, False, False])
        self.assertEqual(blocks[0]['titles'], ['確定タイトル'])
        self.assertEqual(blocks[1]['titles'], ['発表者: 発表者'])

    def test_uses_published_event_instead_of_requested_schedule(self):
        participation, event = self.add_slot(start=time(20, 15), duration=90)
        participation.requested_date = date(2026, 12, 15)
        participation.requested_start_time = time(22)
        participation.requested_duration = 30
        participation.save()
        block = self.first_day_blocks(self.build())[0]
        self.assertEqual(block['event_id'], event.pk)
        self.assertEqual(block['time_label'], '20:15〜21:45')

    def test_unpublished_and_outside_period_slots_are_excluded(self):
        VketParticipation.objects.create(collaboration=self.collaboration, community=make_community())
        self.add_slot(day=self.collaboration.period_start - timedelta(days=1))
        self.add_slot(day=self.collaboration.period_end + timedelta(days=1))
        self.assertEqual(self.build()['scheduled_count'], 0)

    def test_unapproved_community_is_excluded(self):
        participation, _ = self.add_slot()
        participation.community.status = 'pending'
        participation.community.save(update_fields=['status'])
        self.assertEqual(self.build()['scheduled_count'], 0)

    def test_query_count_is_constant_for_many_published_participations(self):
        self.add_slot(theme='発表', detail_type='LT', status='approved')
        with self.assertNumQueries(2):
            self.build()
        for _ in range(29):
            self.add_slot(theme='発表', detail_type='LT', status='approved')
        with self.assertNumQueries(2):
            timetable = self.build()
        self.assertEqual(timetable['scheduled_count'], 30)

    def test_empty_schedule_has_no_invented_time_range(self):
        timetable = self.build()
        self.assertEqual(timetable['ticks'], [])
        self.assertEqual(timetable['scheduled_count'], 0)
