from datetime import date, time

from django.test import Client, TestCase
from django.urls import reverse

from community.models import Community, CommunityMember
from event.models import Event, RecurrenceRule
from user_account.models import CustomUser


class RecurringEventCreateViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = CustomUser.objects.create_user(
            user_name='owner',
            email='owner@example.com',
            password='testpass123',
        )
        self.community = Community.objects.create(
            name='定期開催登録テスト',
            frequency='毎週',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Tue'],
            organizers='主催者',
            status='approved',
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.user,
            role=CommunityMember.Role.OWNER,
        )
        self.url = reverse('event:create_recurring_event', kwargs={'community_id': self.community.pk})

    def test_owner_can_open_recurring_event_form(self):
        self.client.login(username='owner', password='testpass123')

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '定期開催イベントを登録')
        self.assertNotContains(response, 'その他（自由記述）')
        self.assertNotContains(response, 'カスタムルール')

    def test_owner_can_create_weekly_recurring_event_without_llm_key(self):
        self.client.login(username='owner', password='testpass123')

        response = self.client.post(
            self.url,
            {
                'base_date': '2026-05-05',
                'start_time': '22:00',
                'duration': '60',
                'frequency': 'WEEKLY',
                'interval': '1',
                'week_of_month': '',
                'custom_rule': '',
                'end_date': '',
            },
        )

        self.assertEqual(response.status_code, 302)
        rule = RecurrenceRule.objects.get(community=self.community)
        self.assertEqual(rule.start_date, date(2026, 5, 5))
        self.assertTrue(
            Event.objects.filter(
                community=self.community,
                recurrence_rule=rule,
                is_recurring_master=True,
            ).exists()
        )
