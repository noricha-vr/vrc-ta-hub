"""撮影の同意（recording_allowed / recording_policy）と detail_type の API 公開のテスト。"""

from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from event.models import EventDetail
from tests.factories import make_community, make_event, make_event_detail, make_user
from user_account.models import APIKey

RecordingPolicy = EventDetail.RecordingPolicy


def _items(response):
    data = response.data
    return data['results'] if isinstance(data, dict) and 'results' in data else data


def _find(response, pk):
    return next(item for item in _items(response) if item['id'] == pk)


class PublicReadRecordingConsentTest(TestCase):
    """認証不要の読み取り API に撮影の同意が出る。"""

    def setUp(self):
        self.client = APIClient()
        self.community = make_community(name='公開 API の集会', tags=['tech'], recording_allowed=False)
        self.event = make_event(self.community)
        self.detail = make_event_detail(
            self.event, status='approved', recording_policy=RecordingPolicy.FORBIDDEN,
        )

    def test_community_has_recording_allowed(self):
        """/community/ に recording_allowed が出る。"""
        response = self.client.get(reverse('community-detail', kwargs={'pk': self.community.pk}))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIs(response.data['recording_allowed'], False)

    def test_community_default_is_true(self):
        """既定の集会は recording_allowed が true。"""
        community = make_community(name='既定の集会', tags=['tech'])

        response = self.client.get(reverse('community-detail', kwargs={'pk': community.pk}))

        self.assertIs(response.data['recording_allowed'], True)

    def test_event_detail_has_policy_type_and_nested_community_flag(self):
        """/event_detail/ に recording_policy・detail_type・ネストした recording_allowed が出る。"""
        response = self.client.get('/api/v1/event_detail/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        item = _find(response, self.detail.pk)
        self.assertEqual(item['recording_policy'], 'forbidden')
        self.assertEqual(item['detail_type'], 'LT')
        self.assertIs(item['event']['community']['recording_allowed'], False)


class EventDetailAPIKeyRecordingPolicyTest(TestCase):
    """API キーでの /event-details/ の読み書き。"""

    def setUp(self):
        self.client = APIClient()
        owner = make_user(user_name='api_owner', email='api_owner@example.com')
        _key, raw_key = APIKey.create_with_raw_key(user=owner, name='撮影テスト')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {raw_key}')
        self.event = make_event(make_community(name='API キーの集会', owner=owner))
        self.detail = make_event_detail(self.event, status='approved')
        self.list_url = reverse('event-detail-api-list')
        self.detail_url = reverse('event-detail-api-detail', kwargs={'pk': self.detail.pk})

    def _create_payload(self, **extra):
        payload = {
            'event': self.event.pk,
            'detail_type': 'LT',
            'start_time': '22:30:00',
            'duration': 20,
            'speaker': 'API 発表者',
            'theme': 'API 発表',
        }
        payload.update(extra)
        return payload

    def test_read_has_policy_and_type(self):
        """GET に recording_policy と detail_type が出る。"""
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        item = _find(response, self.detail.pk)
        self.assertEqual(item['recording_policy'], 'public')
        self.assertEqual(item['detail_type'], 'LT')

    def test_create_with_policy(self):
        """POST で recording_policy を指定して作れる。"""
        response = self.client.post(self.list_url, self._create_payload(recording_policy='allowed'), format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = EventDetail.objects.get(pk=response.data['id'])
        self.assertEqual(created.recording_policy, RecordingPolicy.ALLOWED)

    def test_create_without_policy_defaults_to_public(self):
        """POST で省略すると「公開」になる。"""
        response = self.client.post(self.list_url, self._create_payload(), format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(EventDetail.objects.get(pk=response.data['id']).recording_policy, RecordingPolicy.PUBLIC)

    def test_patch_policy(self):
        """PATCH で recording_policy だけ変えられる。"""
        response = self.client.patch(self.detail_url, {'recording_policy': 'forbidden'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.recording_policy, RecordingPolicy.FORBIDDEN)

    def test_invalid_policy_is_400(self):
        """選択肢にない値は 400 で、保存されない。"""
        response = self.client.patch(self.detail_url, {'recording_policy': 'secret'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.recording_policy, RecordingPolicy.PUBLIC)
