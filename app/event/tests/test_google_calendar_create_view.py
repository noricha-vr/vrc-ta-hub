from datetime import time

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from community.models import Community, CommunityMember

User = get_user_model()


class GoogleCalendarEventCreateViewTests(TestCase):
    def setUp(self):
        self.url = reverse('event:calendar_create')
        self.user = User.objects.create_user(
            user_name='calendar-user',
            email='calendar-user@example.com',
            password='testpass123',
        )

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('account:login'), response.url)
        self.assertIn(f'next={self.url}', response.url)

    def test_pending_community_user_is_redirected_to_my_list(self):
        community = Community.objects.create(
            name='Pending Community',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='Organizer',
            status='pending',
        )
        CommunityMember.objects.create(
            community=community,
            user=self.user,
            role=CommunityMember.Role.OWNER,
        )
        self.client.login(username='calendar-user', password='testpass123')

        response = self.client.get(self.url, follow=True)

        self.assertRedirects(response, reverse('event:my_list'))
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertIn('集会が承認されていないため、カレンダーにイベントを登録できません。', messages)
