from datetime import timedelta, time

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from community.models import Community
from event.models import Event


class EventAPITest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse('event-list')
        today = timezone.localdate()

        self.community1 = Community.objects.create(
            name='Event Community 1',
            start_time=time(20, 0),
            duration=120,
            weekdays=['Mon'],
            frequency='weekly',
            organizers='Organizer 1',
            status='approved'
        )
        self.community2 = Community.objects.create(
            name='Event Community 2',
            start_time=time(21, 0),
            duration=90,
            weekdays=['Tue'],
            frequency='weekly',
            organizers='Organizer 2',
            status='approved'
        )
        self.pending_community = Community.objects.create(
            name='Pending Event Community',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Wed'],
            frequency='weekly',
            organizers='Organizer 3',
            status='pending'
        )
        self.ended_community = Community.objects.create(
            name='Ended Event Community',
            start_time=time(23, 0),
            duration=60,
            weekdays=['Thu'],
            frequency='weekly',
            organizers='Organizer 4',
            status='approved',
            end_at=today - timedelta(days=1)
        )

        self.event1 = Event.objects.create(
            community=self.community1,
            date=today + timedelta(days=1),
            start_time=time(20, 0),
            duration=120,
            weekday='Mon'
        )
        self.event1_later = Event.objects.create(
            community=self.community1,
            date=today + timedelta(days=8),
            start_time=time(20, 0),
            duration=120,
            weekday='Mon'
        )
        self.event2 = Event.objects.create(
            community=self.community2,
            date=today + timedelta(days=2),
            start_time=time(21, 0),
            duration=90,
            weekday='Tue'
        )
        self.pending_event = Event.objects.create(
            community=self.pending_community,
            date=today + timedelta(days=3),
            start_time=time(22, 0),
            duration=60,
            weekday='Wed'
        )
        self.ended_event = Event.objects.create(
            community=self.ended_community,
            date=today + timedelta(days=4),
            start_time=time(23, 0),
            duration=60,
            weekday='Thu'
        )

    def _response_items(self, response):
        if 'results' in response.data:
            return response.data['results']
        return response.data

    def test_filter_by_community(self):
        response = self.client.get(self.url, {'community': self.community1.id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {item['id'] for item in self._response_items(response)}
        self.assertEqual(ids, {self.event1.id, self.event1_later.id})

        response = self.client.get(self.url, {'community': self.community2.id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {item['id'] for item in self._response_items(response)}
        self.assertEqual(ids, {self.event2.id})

    def test_filter_by_unknown_community_returns_empty(self):
        response = self.client.get(self.url, {'community': 999999})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._response_items(response), [])

    def test_filter_combines_community_and_weekday(self):
        response = self.client.get(
            self.url,
            {'community': self.community1.id, 'weekday': 'Mon'}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {item['id'] for item in self._response_items(response)}
        self.assertEqual(ids, {self.event1.id, self.event1_later.id})

        response = self.client.get(
            self.url,
            {'community': self.community1.id, 'weekday': 'Tue'}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._response_items(response), [])

    def test_filter_combines_community_and_name(self):
        response = self.client.get(
            self.url,
            {'community': self.community2.id, 'name': 'Community 2'}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {item['id'] for item in self._response_items(response)}
        self.assertEqual(ids, {self.event2.id})

        response = self.client.get(
            self.url,
            {'community': self.community2.id, 'name': 'Community 1'}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._response_items(response), [])

    def test_filter_by_hidden_community_returns_empty(self):
        response = self.client.get(self.url, {'community': self.pending_community.id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._response_items(response), [])

        response = self.client.get(self.url, {'community': self.ended_community.id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._response_items(response), [])
