"""読み取り専用の API キー（scope=read）は書き込めない。"""

from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from event.models import EventDetail
from tests.factories import make_community, make_event, make_event_detail, make_user
from user_account.models import APIKey


class APIKeyScopeTest(TestCase):
    def setUp(self):
        self.owner = make_user(user_name='scope_owner', email='scope_owner@example.com')
        self.event = make_event(make_community(name='スコープの集会', owner=self.owner))
        self.detail = make_event_detail(self.event, status='approved', theme='元のテーマ')
        self.list_url = reverse('event-detail-api-list')
        self.detail_url = reverse('event-detail-api-detail', kwargs={'pk': self.detail.pk})

    def _client(self, scope):
        key, raw_key = APIKey.create_with_raw_key(user=self.owner, name=f'{scope} のキー')
        key.scope = scope
        key.save(update_fields=['scope'])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {raw_key}')
        return client

    def _payload(self):
        return {'event': self.event.pk, 'detail_type': 'LT', 'start_time': '22:30:00',
                'duration': 20, 'speaker': '発表者', 'theme': '新しい発表'}

    def test_read_key_can_read(self):
        response = self._client(APIKey.SCOPE_READ).get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_read_key_cannot_write(self):
        client = self._client(APIKey.SCOPE_READ)
        count = EventDetail.objects.count()

        responses = [
            client.post(self.list_url, self._payload(), format='json'),
            client.patch(self.detail_url, {'theme': '書き換え'}, format='json'),
            client.put(self.detail_url, self._payload(), format='json'),
            client.delete(self.detail_url),
        ]

        for response in responses:
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(EventDetail.objects.count(), count)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.theme, '元のテーマ')
        self.assertIsNone(self.detail.deleted_at)

    def test_rejection_has_code_and_does_not_touch_last_used(self):
        key, raw_key = APIKey.create_with_raw_key(user=self.owner, name='read のキー')
        key.scope = APIKey.SCOPE_READ
        key.save(update_fields=['scope'])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {raw_key}')

        response = client.patch(self.detail_url, {'theme': '書き換え'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data['code'], 'read_only_api_key')
        key.refresh_from_db()
        self.assertIsNone(key.last_used)

    def test_unknown_scope_cannot_write(self):
        response = self._client('admin').patch(self.detail_url, {'theme': '書き換え'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_write_key_can_write(self):
        response = self._client(APIKey.SCOPE_WRITE).patch(self.detail_url, {'theme': '書き換え'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.theme, '書き換え')
