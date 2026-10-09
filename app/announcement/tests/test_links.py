"""マイページと Vket の管理画面から、スタッフにだけ予約送信へのリンクを出すテスト。"""
from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tests.factories import make_user
from vket.models import VketCollaboration

LINK_TESTID = 'data-testid="discord-announce-link"'


class MyListLinkTests(TestCase):
    url = 'event:my_list'

    def test_staff_sees_link(self):
        self.client.force_login(make_user(user_name='staff', email='staff@example.com', is_staff=True))

        response = self.client.get(reverse(self.url))

        self.assertContains(response, LINK_TESTID)
        self.assertContains(response, reverse('announcement:discord_list'))

    def test_member_does_not_see_link(self):
        self.client.force_login(make_user(user_name='member', email='member@example.com'))

        response = self.client.get(reverse(self.url))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, LINK_TESTID)


class VketManageLinkTests(TestCase):
    def test_vket_manage_top_links_to_scheduled_messages(self):
        today = timezone.localdate()
        collaboration = VketCollaboration.objects.create(
            slug='vket-link-test',
            name='Vket リンク確認',
            period_start=today,
            period_end=today + timedelta(days=7),
            registration_deadline=today + timedelta(days=1),
            lt_deadline=today + timedelta(days=3),
            phase=VketCollaboration.Phase.ENTRY_OPEN,
        )
        self.client.force_login(make_user(user_name='staff', email='staff@example.com', is_staff=True))

        response = self.client.get(reverse('vket:manage', kwargs={'pk': collaboration.pk}))

        self.assertContains(response, LINK_TESTID)
