from datetime import date, time

from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from community.models import Community
from event.models import Event


class GatheringListAPITest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse('community-gathering-list')

        self.sunday_community = Community.objects.create(
            name='日曜技術学術集会',
            start_time=time(20, 30),
            duration=90,
            weekdays=['Sun'],
            frequency='隔週',
            organizers='主催A・副主催B',
            group_url='https://vrc.group/SUN.0001',
            discord='https://discord.gg/sunday',
            sns_url='https://x.com/sunday',
            twitter_hashtag='#SundayGathering',
            poster_image='poster/sunday.jpg',
            description='日曜日に開催する技術と学術の集会です。',
            status='approved',
            tags=['tech', 'academic'],
        )
        Event.objects.create(
            community=self.sunday_community,
            date=date(2026, 3, 29),
            start_time=time(20, 30),
            duration=90,
            weekday='Sun',
        )

        self.monday_community = Community.objects.create(
            name='月曜学術集会',
            start_time=time(21, 0),
            duration=60,
            weekdays=['Mon', 'Thu'],
            frequency='毎月第1月曜',
            organizers='主催C',
            organizer_url='https://vrchat.com/home/user/usr_monday',
            discord='',
            sns_url='',
            twitter_hashtag='',
            description='',
            status='approved',
            tags=['academic'],
        )

        self.fallback_join_community = Community.objects.create(
            name='火曜技術集会',
            start_time=time(22, 0),
            duration=60,
            weekdays=[],
            frequency='毎週',
            organizers='主催D',
            description='主催者 Join の集会です。',
            status='approved',
            tags=['tech'],
        )

        Community.objects.create(
            name='終了済み集会',
            start_time=time(20, 0),
            duration=60,
            weekdays=['Wed'],
            frequency='毎週',
            organizers='主催E',
            status='approved',
            end_at=date(2026, 3, 1),
            tags=['tech'],
        )
        Community.objects.create(
            name='承認待ち集会',
            start_time=time(20, 0),
            duration=60,
            weekdays=['Thu'],
            frequency='毎週',
            organizers='主催F',
            status='pending',
            tags=['tech'],
        )
        Community.objects.create(
            name='協力団体',
            start_time=time(20, 0),
            duration=60,
            weekdays=['Fri'],
            frequency='毎週',
            organizers='主催G',
            status='approved',
            tags=['partner'],
        )

    def test_gathering_list_returns_sample_json_compatible_format(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        payload = response.json()
        self.assertEqual(len(payload), 3)

        self.assertEqual(payload[0]['ジャンル'], '技術系・学術系')
        self.assertEqual(payload[0]['曜日'], '日曜日')
        self.assertEqual(payload[0]['イベント名'], '日曜技術学術集会')
        self.assertEqual(payload[0]['開始時刻'], '20:30')
        self.assertEqual(payload[0]['開催周期'], '隔週')
        self.assertEqual(payload[0]['主催・副主催'], '主催A・副主催B')
        self.assertEqual(payload[0]['Join先'], 'https://vrc.group/SUN.0001')
        self.assertEqual(payload[0]['Discord'], 'https://discord.gg/sunday')
        self.assertEqual(payload[0]['Twitter'], 'https://x.com/sunday')
        self.assertEqual(payload[0]['ハッシュタグ'], '#SundayGathering')
        self.assertTrue(payload[0]['ポスター'].endswith('/poster/sunday.jpg'))
        self.assertEqual(payload[0]['イベント紹介'], '日曜日に開催する技術と学術の集会です。')

        self.assertEqual(payload[1]['曜日'], '月曜日')
        self.assertEqual(payload[1]['Join先'], 'https://vrchat.com/home/user/usr_monday')
        self.assertIsNone(payload[1]['ポスター'])

        self.assertEqual(payload[2]['曜日'], 'その他')
        self.assertEqual(payload[2]['Join先'], '主催D')

    def test_gathering_list_excludes_non_active_or_non_gathering_communities(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        event_names = [item['イベント名'] for item in response.json()]
        self.assertNotIn('終了済み集会', event_names)
        self.assertNotIn('承認待ち集会', event_names)
        self.assertNotIn('協力団体', event_names)
