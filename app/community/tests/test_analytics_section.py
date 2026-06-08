"""集会詳細ページのアクセス解析セクションの表示・権限テスト.

権限境界の検証が主目的: 他人の集会の集計データが context にもHTMLにも
一切現れないことを assert する。
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from analytics.models import PageAnalytics
from community.models import Community, CommunityMember

User = get_user_model()


class CommunityDetailAnalyticsTests(TestCase):
    """集会A(owner=userA) / 集会B(owner=userB) でアクセス境界を検証する."""

    @classmethod
    def setUpTestData(cls):
        cls.user_a = User.objects.create_user(
            user_name='ownerA', email='a@example.com', password='pass-a'
        )
        cls.user_b = User.objects.create_user(
            user_name='ownerB', email='b@example.com', password='pass-b'
        )
        cls.superuser = User.objects.create_superuser(
            user_name='admin', email='admin@example.com', password='pass-admin'
        )
        cls.community_a = Community.objects.create(
            name='集会A', status='approved', frequency='毎週', organizers='主催A'
        )
        cls.community_b = Community.objects.create(
            name='集会B', status='approved', frequency='毎週', organizers='主催B'
        )
        CommunityMember.objects.create(
            community=cls.community_a, user=cls.user_a,
            role=CommunityMember.Role.OWNER,
        )
        CommunityMember.objects.create(
            community=cls.community_b, user=cls.user_b,
            role=CommunityMember.Role.OWNER,
        )

        # 集計対象は前日まで（当日は GA4 未同期で集計外）。前日にデータを置く
        yesterday = timezone.localdate() - timedelta(days=1)
        # 集会A のアクセスデータ（識別しやすい source_medium で他人混入を検出）
        PageAnalytics.objects.create(
            page_path=f'/community/{cls.community_a.pk}/', date=yesterday,
            content_type=PageAnalytics.ContentType.COMMUNITY,
            community=cls.community_a, object_id=cls.community_a.pk,
            pv=100, users=80, sessions=90, source_medium='source-A-only / organic',
        )
        # 集会B のアクセスデータ
        PageAnalytics.objects.create(
            page_path=f'/community/{cls.community_b.pk}/', date=yesterday,
            content_type=PageAnalytics.ContentType.COMMUNITY,
            community=cls.community_b, object_id=cls.community_b.pk,
            pv=500, users=400, sessions=450, source_medium='source-B-only / referral',
        )

    def setUp(self):
        self.client = Client()

    def _url(self, community):
        return reverse('community:detail', kwargs={'pk': community.pk})

    def test_owner_sees_own_analytics(self):
        """userA が自分の集会Aを見ると集計 context があり canvas が出る."""
        self.client.force_login(self.user_a)
        response = self.client.get(self._url(self.community_a))
        self.assertEqual(response.status_code, 200)
        self.assertIn('daily_series', response.context)
        self.assertIn('source_breakdown', response.context)
        self.assertContains(response, 'id="analytics-daily-chart"')
        self.assertContains(response, 'source-A-only / organic')

    def test_owner_cannot_see_others_analytics(self):
        """userA が他人の集会Bを見ると集計 context が無く、Bのデータも出ない."""
        self.client.force_login(self.user_a)
        response = self.client.get(self._url(self.community_b))
        self.assertEqual(response.status_code, 200)
        # 権限が無いので集計キー自体が context に入らない
        self.assertNotIn('daily_series', response.context)
        self.assertNotIn('source_breakdown', response.context)
        # グラフセクション・他人のデータがHTMLに出ない
        self.assertNotContains(response, 'id="analytics-daily-chart"')
        self.assertNotContains(response, 'source-B-only / referral')

    def test_superuser_sees_any_analytics(self):
        """superuser は集会A・B の両方で集計が見える."""
        self.client.force_login(self.superuser)
        response_a = self.client.get(self._url(self.community_a))
        self.assertIn('daily_series', response_a.context)
        self.assertContains(response_a, 'source-A-only / organic')

        response_b = self.client.get(self._url(self.community_b))
        self.assertIn('daily_series', response_b.context)
        self.assertContains(response_b, 'source-B-only / referral')

    def test_superuser_sees_analytics_before_owner_contact_card(self):
        """superuser 画面ではアクセス解析が集会主催者の連絡先より先に出る."""
        self.client.force_login(self.superuser)
        response = self.client.get(self._url(self.community_a))
        html = response.content.decode()
        analytics_index = html.index('id="analytics-section"')
        owner_contact_index = html.index('集会主催者の連絡先')
        self.assertLess(analytics_index, owner_contact_index)

    def test_anonymous_has_no_analytics(self):
        """匿名ユーザーには集計 context もグラフセクションも無い."""
        response = self.client.get(self._url(self.community_a))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('daily_series', response.context)
        self.assertNotContains(response, 'id="analytics-daily-chart"')

    def test_chart_scripts_loaded_only_for_owner(self):
        """Chart.js は権限がある画面でのみ読み込まれる."""
        chart_cdn = 'cdn.jsdelivr.net/npm/chart.js'
        anon_response = self.client.get(self._url(self.community_a))
        self.assertNotContains(anon_response, chart_cdn)

        self.client.force_login(self.user_a)
        owner_response = self.client.get(self._url(self.community_a))
        self.assertContains(owner_response, chart_cdn)

    def test_staff_sees_own_community_analytics(self):
        """staff として登録されたユーザーが自集会の解析を見られる（owner と同等）."""
        staff_user = User.objects.create_user(
            user_name='staffA', email='staff_a@example.com', password='pass-s',
        )
        CommunityMember.objects.create(
            community=self.community_a, user=staff_user,
            role=CommunityMember.Role.STAFF,
        )
        self.client.force_login(staff_user)
        response = self.client.get(self._url(self.community_a))
        self.assertEqual(response.status_code, 200)
        self.assertIn('daily_series', response.context)
        self.assertIn('source_breakdown', response.context)
        self.assertContains(response, 'id="analytics-daily-chart"')
        self.assertContains(response, 'source-A-only / organic')

    def test_staff_cannot_see_other_community_analytics(self):
        """集会Aのstaffが集会Bを見ても、Bのデータは漏れない（権限境界）."""
        staff_user = User.objects.create_user(
            user_name='staffA2', email='staff_a2@example.com', password='pass-s',
        )
        CommunityMember.objects.create(
            community=self.community_a, user=staff_user,
            role=CommunityMember.Role.STAFF,
        )
        self.client.force_login(staff_user)
        response = self.client.get(self._url(self.community_b))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('daily_series', response.context)
        self.assertNotIn('source_breakdown', response.context)
        self.assertNotContains(response, 'source-B-only / referral')

    def test_staff_does_not_see_edit_button(self):
        """staff には集会編集ボタンは表示されない（is_owner ガード維持の回帰防止）."""
        staff_user = User.objects.create_user(
            user_name='staffA3', email='staff_a3@example.com', password='pass-s',
        )
        CommunityMember.objects.create(
            community=self.community_a, user=staff_user,
            role=CommunityMember.Role.STAFF,
        )
        self.client.force_login(staff_user)
        response = self.client.get(self._url(self.community_a))
        # is_owner は False のはず（編集ボタン用 context キー）
        self.assertFalse(response.context['is_owner'])
        # アクセス解析は見える
        self.assertTrue(response.context['can_view_analytics'])

    def test_source_medium_is_json_escaped(self):
        """source_medium に危険な文字があっても json_script でエスケープされる（XSS回帰）."""
        # </script> を含む source_medium は GA4 由来データに混入し得る。
        # 別 date にして unique_together (page_path, date, source_medium) 衝突を避ける。
        other_date = timezone.localdate() - timedelta(days=1)
        PageAnalytics.objects.create(
            page_path=f'/community/{self.community_a.pk}/', date=other_date,
            content_type=PageAnalytics.ContentType.COMMUNITY,
            community=self.community_a, object_id=self.community_a.pk,
            pv=1, users=1, sessions=1,
            source_medium='</script><img src=x onerror=alert(1)>',
        )
        self.client.force_login(self.user_a)
        response = self.client.get(self._url(self.community_a))
        # 生の </script> や onerror 属性がそのまま出力されない
        # （json_script は <, >, & を \u00XX にエスケープする）
        self.assertNotContains(response, '</script><img src=x onerror=alert(1)>')
