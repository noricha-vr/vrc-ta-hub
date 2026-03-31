from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from datetime import time

from community.models import Community


class CommunityAPITest(TestCase):
    """CommunityViewSet の公開APIテスト"""

    def setUp(self):
        self.client = APIClient()
        self.list_url = reverse('community-list')

        # 正常なコミュニティ（tags付き、approved）
        self.approved_with_tags = Community.objects.create(
            name='技術集会A',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='毎週',
            organizers='主催者A',
            group_url='https://vrc.group/TECH.0001',
            tags=['tech'],
            status='approved',
            allow_poster_repost=True,
        )

        # tags空のコミュニティ（approved）→ 除外されるべき
        self.approved_no_tags = Community.objects.create(
            name='TestUser Community',
            start_time=time(22, 0),
            duration=60,
            weekdays=['Mon'],
            frequency='Every week',
            organizers='testuser',
            tags=[],
            status='approved',
        )

        # pending のコミュニティ → 除外されるべき
        self.pending = Community.objects.create(
            name='承認待ち集会',
            start_time=time(21, 0),
            duration=60,
            weekdays=['Tue'],
            frequency='毎週',
            organizers='主催者B',
            tags=['tech'],
            status='pending',
        )

        # 終了済みコミュニティ → 除外されるべき
        self.ended = Community.objects.create(
            name='終了した集会',
            start_time=time(20, 0),
            duration=60,
            weekdays=['Wed'],
            frequency='毎週',
            organizers='主催者C',
            tags=['academic'],
            status='approved',
            end_at='2025-12-31',
        )

    def test_approved_with_tags_is_listed(self):
        """tags付きのapprovedコミュニティがAPIに含まれる"""
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = [c['name'] for c in response.data]
        self.assertIn('技術集会A', names)

    def test_approved_no_tags_is_excluded(self):
        """tags空のapprovedコミュニティがAPIに含まれない"""
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = [c['name'] for c in response.data]
        self.assertNotIn('TestUser Community', names)

    def test_pending_is_excluded(self):
        """pendingコミュニティがAPIに含まれない"""
        response = self.client.get(self.list_url)
        names = [c['name'] for c in response.data]
        self.assertNotIn('承認待ち集会', names)

    def test_ended_is_excluded(self):
        """終了済みコミュニティがAPIに含まれない"""
        response = self.client.get(self.list_url)
        names = [c['name'] for c in response.data]
        self.assertNotIn('終了した集会', names)

    def test_group_id_extracted_from_short_url(self):
        """group_urlからgroup_idが抽出される（短縮URL）"""
        response = self.client.get(self.list_url)
        community = next(c for c in response.data if c['name'] == '技術集会A')
        self.assertEqual(community['group_id'], 'TECH.0001')

    def test_group_id_none_when_no_url(self):
        """group_urlが空の場合group_idはnull"""
        Community.objects.create(
            name='URL無し集会',
            start_time=time(21, 0),
            duration=60,
            weekdays=['Tue'],
            frequency='毎週',
            organizers='主催者X',
            group_url='',
            tags=['tech'],
            status='approved',
        )
        response = self.client.get(self.list_url)
        community = next(c for c in response.data if c['name'] == 'URL無し集会')
        self.assertIsNone(community['group_id'])

    def test_start_time_format_without_seconds(self):
        """start_timeが秒なしのHH:MM形式で返される"""
        response = self.client.get(self.list_url)
        community = next(c for c in response.data if c['name'] == '技術集会A')
        self.assertEqual(community['start_time'], '22:00')

    def test_allow_poster_repost_included(self):
        """allow_poster_repostがレスポンスに含まれる"""
        response = self.client.get(self.list_url)
        community = next(c for c in response.data if c['name'] == '技術集会A')
        self.assertTrue(community['allow_poster_repost'])
