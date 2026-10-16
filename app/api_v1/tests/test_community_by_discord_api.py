"""Discord ID による所属集会の取得はスーパーユーザーに限定する。"""

from allauth.socialaccount.models import SocialAccount
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from community.models import CommunityMember
from tests.factories import make_community, make_discord_linked_user
from user_account.models import APIKey


class CommunityByDiscordAPITest(TestCase):
    discord_id = '123456789012345678'

    @classmethod
    def setUpTestData(cls):
        cls.admin = make_discord_linked_user(
            user_name='discord_lookup_admin', email='lookup_admin@example.com',
            is_superuser=True, is_staff=True,
        )
        cls.organizer = make_discord_linked_user(
            user_name='discord_organizer', email='organizer@example.com', discord_uid=cls.discord_id,
        )
        cls.owner_community = make_community(name='主催する集会', recording_allowed=False)
        cls.staff_community = make_community(name='スタッフの集会', recording_allowed=True)
        # 所属の作成順とは無関係に、集会 ID 順で返す。
        CommunityMember.objects.create(
            community=cls.staff_community, user=cls.organizer, role=CommunityMember.Role.STAFF,
        )
        CommunityMember.objects.create(
            community=cls.owner_community, user=cls.organizer, role=CommunityMember.Role.OWNER,
        )
        make_community(name='所属しない集会', owner=cls.admin)
        cls.non_member = make_discord_linked_user(
            user_name='discord_non_member', email='non_member@example.com',
            discord_uid='234567890123456789',
        )
        cls.staff = make_discord_linked_user(
            user_name='discord_site_staff', email='site_staff@example.com', is_staff=True,
        )
        key, cls.raw_key = APIKey.create_with_raw_key(user=cls.admin, name='所属集会の参照')
        key.scope = APIKey.SCOPE_READ
        key.save(update_fields=['scope'])

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client = APIClient()
        self.client.credentials(HTTP_X_API_KEY=self.raw_key)
        self.url = reverse('community-by-discord')

    def _expected_payload(self):
        return {
            'discord_id': self.discord_id,
            'communities': [
                {
                    'id': self.owner_community.pk,
                    'name': self.owner_community.name,
                    'role': 'owner',
                    'recording_allowed': False,
                },
                {
                    'id': self.staff_community.pk,
                    'name': self.staff_community.name,
                    'role': 'staff',
                    'recording_allowed': True,
                },
            ],
        }

    def test_superuser_api_key_returns_only_memberships_and_public_fields(self):
        response = self.client.get(self.url, {'discord_id': self.discord_id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), self._expected_payload())
        self.assertEqual(self.url, '/api/v1/community-by-discord/')

    def test_superuser_session_can_read(self):
        self.client.credentials()
        self.client.force_login(self.admin)

        response = self.client.get(self.url, {'discord_id': self.discord_id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), self._expected_payload())

    def test_non_member_and_unknown_id_return_empty_list(self):
        for discord_id in ['234567890123456789', '345678901234567890']:
            with self.subTest(discord_id=discord_id):
                response = self.client.get(self.url, {'discord_id': discord_id})

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.json(), {'discord_id': discord_id, 'communities': []})

    def test_other_provider_does_not_match(self):
        discord_id = '456789012345678901'
        SocialAccount.objects.create(user=self.organizer, provider='github', uid=discord_id)

        response = self.client.get(self.url, {'discord_id': discord_id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), {'discord_id': discord_id, 'communities': []})

    def test_valid_id_lengths(self):
        for length in range(17, 21):
            discord_id = '9' * length
            with self.subTest(length=length):
                response = self.client.get(self.url, {'discord_id': discord_id})

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.json(), {'discord_id': discord_id, 'communities': []})

    def test_invalid_id_returns_400(self):
        invalid_ids = [
            '', '1' * 16, '1' * 21, 'a' * 18, '12345678901234567a',
            '１２３４５６７８９０１２３４５６７８', '١' * 18,
            f' {self.discord_id}', f'{self.discord_id} ', f'{self.discord_id}\n',
            f'+{self.discord_id}', f'-{self.discord_id}',
        ]
        for discord_id in invalid_ids:
            with self.subTest(discord_id=discord_id):
                response = self.client.get(self.url, {'discord_id': discord_id})

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn('discord_id', response.json()['detail'])

    def test_missing_id_returns_400(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('discord_id', response.json()['detail'])

    def test_non_superuser_api_keys_are_forbidden(self):
        for user in [self.organizer, self.staff, self.non_member]:
            with self.subTest(user=user.user_name):
                _, raw_key = APIKey.create_with_raw_key(user=user, name='権限の確認')
                self.client.credentials(HTTP_X_API_KEY=raw_key)

                response = self.client.get(self.url, {'discord_id': self.discord_id})

                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
                self.assertNotIn('communities', response.json())

    def test_non_superuser_session_is_forbidden(self):
        self.client.credentials()
        self.client.force_login(self.staff)

        response = self.client.get(self.url, {'discord_id': self.discord_id})

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_permission_is_checked_before_id_validation(self):
        _, raw_key = APIKey.create_with_raw_key(user=self.organizer, name='検証順の確認')
        self.client.credentials(HTTP_X_API_KEY=raw_key)

        response = self.client.get(self.url, {'discord_id': 'invalid'})

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_request_is_denied(self):
        self.client.credentials()

        response = self.client.get(self.url, {'discord_id': self.discord_id})

        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])
        self.assertNotIn('communities', response.json())

    def test_write_methods_are_not_allowed(self):
        self.client.credentials()
        self.client.force_login(self.admin)

        for method in ['post', 'put', 'patch', 'delete']:
            with self.subTest(method=method):
                response = getattr(self.client, method)(self.url, {'discord_id': self.discord_id})

                self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

