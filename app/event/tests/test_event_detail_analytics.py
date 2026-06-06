"""イベント詳細ページのアクセス解析セクションの表示・権限テスト.

can_manage_event_detail の真偽で集計 context の有無が切り替わること、
別集会のオーナーには当該 event_detail の集計が出ないことを検証する。
"""
from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from analytics.models import PageAnalytics
from community.models import Community, CommunityMember
from event.models import Event, EventDetail

User = get_user_model()


class EventDetailAnalyticsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            user_name='ev_owner', email='owner@example.com', password='pass'
        )
        cls.other_owner = User.objects.create_user(
            user_name='ev_other', email='other@example.com', password='pass'
        )
        cls.superuser = User.objects.create_superuser(
            user_name='ev_admin', email='admin@example.com', password='pass'
        )
        # 集会管理者ではないが、自分の承認済みLTの応募者本人（編集権限はあるが
        # アクセス解析の閲覧権限は持たないことを検証するためのユーザー）
        cls.applicant = User.objects.create_user(
            user_name='ev_applicant', email='applicant@example.com', password='pass'
        )

        cls.community = Community.objects.create(
            name='対象集会', status='approved', frequency='毎週', organizers='主催',
        )
        cls.other_community = Community.objects.create(
            name='別集会', status='approved', frequency='毎週', organizers='別主催',
        )
        CommunityMember.objects.create(
            community=cls.community, user=cls.owner,
            role=CommunityMember.Role.OWNER,
        )
        CommunityMember.objects.create(
            community=cls.other_community, user=cls.other_owner,
            role=CommunityMember.Role.OWNER,
        )

        cls.event = Event.objects.create(
            community=cls.community,
            date=date(2026, 2, 10),
            start_time=time(22, 0),
            duration=60,
            weekday='Tue',
        )
        cls.event_detail = EventDetail.objects.create(
            event=cls.event,
            detail_type='LT',
            start_time=time(22, 0),
            duration=30,
            speaker='Speaker',
            theme='Theme',
            contents='contents',
            status='approved',
            applicant=cls.applicant,
        )

        # 集計対象は前日まで（当日は GA4 未同期で集計外）。前日にデータを置く
        yesterday = timezone.localdate() - timedelta(days=1)
        PageAnalytics.objects.create(
            page_path=f'/event/detail/{cls.event_detail.pk}/', date=yesterday,
            content_type=PageAnalytics.ContentType.EVENT_DETAIL,
            community=cls.community, object_id=cls.event_detail.pk,
            pv=42, users=30, sessions=35, source_medium='event-source-only / organic',
        )

    def setUp(self):
        self.client = Client()

    def _url(self):
        return reverse('event:detail', kwargs={'pk': self.event_detail.pk})

    def test_manager_sees_analytics(self):
        """管理権限ありで集計 context が入り canvas が出る."""
        self.client.force_login(self.owner)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_manage_event_detail'])
        self.assertIn('daily_series', response.context)
        self.assertIn('source_breakdown', response.context)
        self.assertContains(response, 'id="analytics-daily-chart"')
        self.assertContains(response, 'event-source-only / organic')

    def test_other_community_owner_has_no_analytics(self):
        """別集会のオーナーには当該 event_detail の集計が出ない."""
        self.client.force_login(self.other_owner)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_view_analytics'])
        self.assertNotIn('daily_series', response.context)
        self.assertNotIn('source_breakdown', response.context)
        self.assertNotContains(response, 'id="analytics-daily-chart"')
        self.assertNotContains(response, 'event-source-only / organic')

    def test_applicant_cannot_see_analytics(self):
        """承認済みLTの応募者本人（集会管理者でない）には集計が出ない.

        can_manage_event_detail は応募者本人を含むため、専用の閲覧判定で
        分離していることを検証する（集会全体の流入元の漏えい防止）。
        """
        self.client.force_login(self.applicant)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        # LT編集権限はあるが、解析閲覧権限は無い
        self.assertTrue(response.context['can_manage_event_detail'])
        self.assertFalse(response.context['can_view_analytics'])
        self.assertNotIn('daily_series', response.context)
        self.assertNotIn('source_breakdown', response.context)
        self.assertNotContains(response, 'id="analytics-daily-chart"')
        self.assertNotContains(response, 'event-source-only / organic')

    def test_superuser_sees_analytics(self):
        """superuser は集計が見える."""
        self.client.force_login(self.superuser)
        response = self.client.get(self._url())
        self.assertIn('daily_series', response.context)
        self.assertContains(response, 'event-source-only / organic')

    def test_anonymous_has_no_analytics(self):
        """匿名ユーザーには集計 context もグラフも無い."""
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_view_analytics'])
        self.assertNotIn('daily_series', response.context)
        self.assertNotContains(response, 'id="analytics-daily-chart"')
