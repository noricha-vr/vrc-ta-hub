from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from django.utils import timezone

from community.models import Community, CommunityMember

from analytics.models import PageAnalytics
from analytics import services

User = get_user_model()


class AccessibleCommunityIdsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user_a = User.objects.create_user(
            user_name='userA', email='a@example.com', password='pass-a'
        )
        cls.user_b = User.objects.create_user(
            user_name='userB', email='b@example.com', password='pass-b'
        )
        cls.superuser = User.objects.create_superuser(
            user_name='admin', email='admin@example.com', password='pass-admin'
        )
        cls.community_a = Community.objects.create(
            name='集会A', frequency='毎週', organizers='主催A'
        )
        cls.community_b = Community.objects.create(
            name='集会B', frequency='毎週', organizers='主催B'
        )
        CommunityMember.objects.create(
            community=cls.community_a, user=cls.user_a,
            role=CommunityMember.Role.OWNER,
        )
        CommunityMember.objects.create(
            community=cls.community_b, user=cls.user_b,
            role=CommunityMember.Role.OWNER,
        )

    def test_owner_only_sees_own_community(self):
        ids = services.accessible_community_ids(self.user_a)
        self.assertIn(self.community_a.id, ids)
        self.assertNotIn(self.community_b.id, ids)

    def test_staff_member_can_access(self):
        staff_user = User.objects.create_user(
            user_name='staffA', email='staff@example.com', password='pass'
        )
        CommunityMember.objects.create(
            community=self.community_a, user=staff_user,
            role=CommunityMember.Role.STAFF,
        )
        ids = services.accessible_community_ids(staff_user)
        self.assertIn(self.community_a.id, ids)

    def test_superuser_sees_all(self):
        ids = services.accessible_community_ids(self.superuser)
        self.assertIn(self.community_a.id, ids)
        self.assertIn(self.community_b.id, ids)

    def test_anonymous_user_sees_nothing(self):
        self.assertEqual(services.accessible_community_ids(AnonymousUser()), [])

    def test_none_user_sees_nothing(self):
        self.assertEqual(services.accessible_community_ids(None), [])


class AggregationPermissionBoundaryTest(TestCase):
    """集計クエリが community 境界を越えないことを検証する（最重要）。"""

    @classmethod
    def setUpTestData(cls):
        cls.user_a = User.objects.create_user(
            user_name='userA', email='a@example.com', password='pass-a'
        )
        cls.community_a = Community.objects.create(
            name='集会A', frequency='毎週', organizers='主催A'
        )
        cls.community_b = Community.objects.create(
            name='集会B', frequency='毎週', organizers='主催B'
        )
        CommunityMember.objects.create(
            community=cls.community_a, user=cls.user_a,
            role=CommunityMember.Role.OWNER,
        )
        # 集計対象は前日まで（当日は GA4 未同期で集計外）。前日にデータを置く
        yesterday = timezone.localdate() - timedelta(days=1)
        PageAnalytics.objects.create(
            page_path=f'/community/{cls.community_a.pk}/', date=yesterday,
            content_type=PageAnalytics.ContentType.COMMUNITY,
            community=cls.community_a, object_id=cls.community_a.pk,
            pv=100, users=80, sessions=90, source_medium='google / organic',
        )
        PageAnalytics.objects.create(
            page_path=f'/community/{cls.community_b.pk}/', date=yesterday,
            content_type=PageAnalytics.ContentType.COMMUNITY,
            community=cls.community_b, object_id=cls.community_b.pk,
            pv=500, users=400, sessions=450, source_medium='twitter / referral',
        )

    def test_daily_series_excludes_other_community(self):
        ids = services.accessible_community_ids(self.user_a)
        series = services.get_daily_series(ids)
        total_pv = sum(row['pv'] for row in series)
        # community_b の pv=500 が混入していないこと
        self.assertEqual(total_pv, 100)

    def test_source_breakdown_excludes_other_community(self):
        ids = services.accessible_community_ids(self.user_a)
        breakdown = services.get_source_breakdown(ids)
        sources = {row['source_medium'] for row in breakdown}
        self.assertIn('google / organic', sources)
        # community_b 由来の参照元が出てこないこと
        self.assertNotIn('twitter / referral', sources)

    def test_empty_community_ids_returns_nothing(self):
        # 未ログイン相当（空リスト）では何も返らない
        self.assertEqual(services.get_daily_series([]), [])
        self.assertEqual(services.get_source_breakdown([]), [])

    def test_object_id_filter_still_respects_community_boundary(self):
        # community_a と community_b が偶然同じ object_id を持つ行を作る。
        # object_id だけで絞ると両方混入するが、community 境界が効いていれば
        # アクセス可能な community_a の分だけが返ることを検証する。
        shared_object_id = 7777
        # 集計対象は前日まで（当日は GA4 未同期で集計外）。前日にデータを置く
        yesterday = timezone.localdate() - timedelta(days=1)
        PageAnalytics.objects.create(
            page_path='/community/a-shared/', date=yesterday,
            content_type=PageAnalytics.ContentType.COMMUNITY,
            community=self.community_a, object_id=shared_object_id,
            pv=11, users=10, sessions=10, source_medium='a / src',
        )
        PageAnalytics.objects.create(
            page_path='/community/b-shared/', date=yesterday,
            content_type=PageAnalytics.ContentType.COMMUNITY,
            community=self.community_b, object_id=shared_object_id,
            pv=22, users=20, sessions=20, source_medium='b / src',
        )
        ids = services.accessible_community_ids(self.user_a)
        series = services.get_daily_series(ids, object_id=shared_object_id)
        total_pv = sum(row['pv'] for row in series)
        # community_b の pv=22 が混入していないこと（community_a の 11 のみ）
        self.assertEqual(total_pv, 11)
