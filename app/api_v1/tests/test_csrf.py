"""CSRF 振る舞いの回帰テスト.

`api_v1` の ViewSet で `csrf_exempt` を削除したため:
- APIKey 認証経由のリクエストは CSRF token なしで POST/PUT/DELETE できること
- 公開 ReadOnlyModelViewSet の GET は CSRF チェック対象外で 200 を返すこと
- SessionAuthentication 経由の POST は DRF 標準動作により CSRF token を要求すること

`APIClient` のデフォルトでは `enforce_csrf_checks=False` のため、SessionAuth の CSRF
振る舞いを検証する場合は `enforce_csrf_checks=True` を明示する。
"""

from datetime import date, time

from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from community.models import Community, CommunityMember
from event.models import Event, EventDetail
from user_account.models import APIKey, CustomUser


class CsrfBehaviorTests(TestCase):
    """`api_v1` の CSRF 関連の振る舞いを検証する."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            user_name='csrf_test_user',
            email='csrf_test@example.com',
            password='testpass123',
        )
        self.api_key_obj, self.raw_api_key = APIKey.create_with_raw_key(
            user=self.user,
            name='CSRF Test API Key',
        )

        self.community = Community.objects.create(
            name='CSRF Test Community',
            start_time=time(20, 0),
            duration=120,
            weekdays='mon',
            frequency='weekly',
            organizers='CSRF Test Organizer',
            status='approved',
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.user,
            role=CommunityMember.Role.OWNER,
        )

        self.event = Event.objects.create(
            community=self.community,
            date=date(2099, 12, 25),
            start_time=time(20, 0),
            duration=120,
            weekday='wed',
        )
        self.event_detail = EventDetail.objects.create(
            event=self.event,
            detail_type='LT',
            start_time=time(20, 0),
            duration=30,
            speaker='CSRF Speaker',
            theme='CSRF Theme',
            h1='CSRF Title',
            contents='CSRF contents',
        )

        self.event_detail_api_url = reverse('event-detail-api-list')
        self.event_detail_api_detail_url = reverse(
            'event-detail-api-detail', kwargs={'pk': self.event_detail.id}
        )

    def _build_event_detail_payload(self):
        return {
            'event': self.event.id,
            'detail_type': 'LT',
            'start_time': '20:30:00',
            'duration': 20,
            'speaker': 'Posted Speaker',
            'theme': 'Posted Theme',
            'h1': 'Posted Title',
            'contents': 'Posted contents',
            'generate_from_pdf': False,
        }

    def test_api_key_post_succeeds_without_csrf_token(self):
        """APIKey 認証経由なら CSRF token なしで POST が通る (CSRF 強制下でも)."""
        client = APIClient(enforce_csrf_checks=True)
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.raw_api_key}')

        response = client.post(
            self.event_detail_api_url,
            self._build_event_detail_payload(),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_api_key_put_and_delete_succeed_without_csrf_token(self):
        """APIKey 認証経由なら CSRF token なしで PUT/DELETE が通る."""
        client = APIClient(enforce_csrf_checks=True)
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.raw_api_key}')

        put_payload = {
            'event': self.event.id,
            'detail_type': 'SPECIAL',
            'start_time': '20:00:00',
            'duration': 45,
            'speaker': 'Updated Speaker',
            'theme': 'Updated Theme',
            'h1': 'Updated Title',
            'contents': 'Updated contents',
        }
        put_response = client.put(
            self.event_detail_api_detail_url, put_payload, format='json'
        )
        self.assertEqual(put_response.status_code, status.HTTP_200_OK)

        delete_response = client.delete(self.event_detail_api_detail_url)
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)

    def test_session_auth_post_without_csrf_token_is_forbidden(self):
        """SessionAuth 経由の POST は CSRF token なしだと 403 で拒否される."""
        client = APIClient(enforce_csrf_checks=True)
        # `force_login` は session を張るが CSRF cookie/token は付与しないため、
        # DRF の `SessionAuthentication.enforce_csrf` が 403 を返すことを確認する。
        client.force_login(self.user)

        response = client.post(
            self.event_detail_api_url,
            self._build_event_detail_payload(),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_session_auth_post_without_csrf_check_succeeds(self):
        """`enforce_csrf_checks=False` の APIClient なら SessionAuth でも POST が通る.

        これは既存のテストが force_authenticate / force_login で POST しているケースを
        壊さないことを示す回帰テスト。
        """
        client = APIClient(enforce_csrf_checks=False)
        client.force_login(self.user)

        response = client.post(
            self.event_detail_api_url,
            self._build_event_detail_payload(),
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_public_readonly_endpoints_allow_get_without_csrf(self):
        """csrf_exempt を削除した公開 ReadOnlyModelViewSet の GET は 200 を返す.

        GET メソッドは CSRF チェック対象外なので、`csrf_exempt` を外しても
        匿名ユーザーがそのまま読めることを保証する。
        """
        client = APIClient(enforce_csrf_checks=True)

        for url in (
            '/api/v1/community/',
            '/api/v1/event/',
            '/api/v1/event_detail/',
        ):
            with self.subTest(url=url):
                response = client.get(url)
                self.assertEqual(
                    response.status_code,
                    status.HTTP_200_OK,
                    msg=f'GET {url} should be publicly accessible without CSRF token',
                )
