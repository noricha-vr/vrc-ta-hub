"""集計期間の境界値テスト（前日基準・当日除外）。

GA4 同期は午前1時に前日分までしか取得しないため、集計対象は
「前日(today-1) から遡った N 日間」= today-N 〜 today-1 のちょうど N 日。
当日(today) は集計に含めない。timezone.localdate() を固定して境界を厳密に検証する。

このファイルは「当日除外・N日ぴったり」仕様の正本テスト。他アプリの集計テストは
前日にデータを置く前提に揃えてあり当日除外そのものは検証しないため、本ファイルを
削除・縮小すると当日除外の回帰保証が失われる点に注意。
"""
from datetime import date, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from community.models import Community, CommunityMember

from analytics import services
from analytics.models import Campaign, PageAnalytics, PosterClick

User = get_user_model()

# localdate を固定して境界を再現可能にする（Date.now 系は使えないため定数で渡す）
FIXED_TODAY = date(2026, 6, 6)


def _patch_today():
    """services 内で参照される timezone.localdate のみ FIXED_TODAY に固定する。"""
    return mock.patch.object(
        services.timezone, 'localdate', return_value=FIXED_TODAY,
    )


class DateRangeHelperTest(TestCase):
    """_date_range が「前日まで・ちょうど N 日」を返すことを検証。"""

    def test_range_is_exactly_n_days_ending_yesterday(self):
        for days in (7, 30):
            with self.subTest(days=days), _patch_today():
                since, until = services._date_range(days)
                self.assertEqual(until, FIXED_TODAY - timedelta(days=1))
                self.assertEqual(since, FIXED_TODAY - timedelta(days=days))
                # 両端含めて ちょうど days 日
                self.assertEqual((until - since).days, days - 1)


class DailySeriesBoundaryTest(TestCase):
    """get_daily_series / get_overall_stats / get_campaign_breakdown の境界を検証。"""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            user_name='owner', email='owner@example.com', password='pass',
        )
        cls.community = Community.objects.create(
            name='集会', frequency='毎週', organizers='主催',
        )
        CommunityMember.objects.create(
            community=cls.community, user=cls.user,
            role=CommunityMember.Role.OWNER,
        )

    def _make(self, target_date, *, pv=1, campaign='cmp-a'):
        PageAnalytics.objects.create(
            page_path=f'/community/{self.community.pk}/',
            date=target_date,
            content_type=PageAnalytics.ContentType.COMMUNITY,
            community=self.community, object_id=self.community.pk,
            pv=pv, users=pv, sessions=pv,
            source_medium='google / organic', campaign=campaign,
        )

    def test_today_record_is_excluded(self):
        """当日(today) のレコードは集計に含まれない（防御的: 手動同期等で入っても表示がブレない）。"""
        self._make(FIXED_TODAY, pv=100)  # 当日 → 除外されるべき
        ids = services.accessible_community_ids(self.user)
        with _patch_today():
            series = services.get_daily_series(ids, days=30)
            stats = services.get_overall_stats(ids, days=30)
        self.assertEqual(sum(r['pv'] for r in series), 0)
        self.assertEqual(stats['pv'], 0)

    def test_yesterday_record_is_included(self):
        """前日(today-1) は集計に含まれる。"""
        self._make(FIXED_TODAY - timedelta(days=1), pv=42)
        ids = services.accessible_community_ids(self.user)
        with _patch_today():
            series = services.get_daily_series(ids, days=30)
        self.assertEqual(sum(r['pv'] for r in series), 42)

    def test_lower_boundary_is_exactly_n_days(self):
        """下端 today-N は含み、today-N-1 は含まない（N日ぴったり）。"""
        for days in (7, 30):
            with self.subTest(days=days):
                PageAnalytics.objects.all().delete()
                self._make(FIXED_TODAY - timedelta(days=days), pv=7)       # 下端: 含む
                self._make(FIXED_TODAY - timedelta(days=days + 1), pv=99)  # 範囲外: 含まない
                ids = services.accessible_community_ids(self.user)
                with _patch_today():
                    series = services.get_daily_series(ids, days=days)
                self.assertEqual(sum(r['pv'] for r in series), 7)

    def test_current_and_prev_periods_are_symmetric(self):
        """current(today-N〜today-1) と prev(today-2N〜today-N-1) が同じ期間長で集計される。"""
        days = 30
        # current 期間の中央付近と prev 期間の中央付近に同じ pv を1件ずつ
        self._make(FIXED_TODAY - timedelta(days=10), pv=50)   # current 内
        self._make(FIXED_TODAY - timedelta(days=40), pv=50)   # prev 内（today-40 ∈ [today-60, today-31]）
        ids = services.accessible_community_ids(self.user)
        with _patch_today():
            stats = services.get_overall_stats(ids, days=days)
        self.assertEqual(stats['pv'], 50)
        self.assertEqual(stats['pv_prev'], 50)
        # 期間長が等しいので比較率は 0%
        self.assertEqual(stats['pv_change_pct'], 0.0)

    def test_prev_boundary_does_not_leak_into_current(self):
        """prev 上端 today-N-1 は current に入らない（境界の取り違え防止）。"""
        days = 30
        self._make(FIXED_TODAY - timedelta(days=days + 1), pv=11)  # = today-31 → prev 側
        ids = services.accessible_community_ids(self.user)
        with _patch_today():
            stats = services.get_overall_stats(ids, days=days)
        self.assertEqual(stats['pv'], 0)
        self.assertEqual(stats['pv_prev'], 11)

    def test_campaign_breakdown_excludes_today(self):
        """get_campaign_breakdown も当日を除外する。"""
        self._make(FIXED_TODAY, pv=100, campaign='cmp-today')
        self._make(FIXED_TODAY - timedelta(days=1), pv=5, campaign='cmp-yesterday')
        ids = services.accessible_community_ids(self.user)
        with _patch_today():
            rows = services.get_campaign_breakdown(ids, days=30)
        keys = {r['campaign'] for r in rows}
        self.assertIn('cmp-yesterday', keys)
        self.assertNotIn('cmp-today', keys)


class CampaignDailySeriesBoundaryTest(TestCase):
    """get_campaign_daily_series のラベルが「前日まで・N個」であることを検証。"""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            user_name='owner2', email='owner2@example.com', password='pass',
        )
        cls.community = Community.objects.create(
            name='集会2', frequency='毎週', organizers='主催',
        )
        CommunityMember.objects.create(
            community=cls.community, user=cls.user,
            role=CommunityMember.Role.OWNER,
        )
        Campaign.objects.create(
            community=cls.community, name='キャンペーン',
            utm_source='flyer', utm_medium='qr', utm_campaign='cmp-a',
        )

    def test_labels_count_and_endpoints(self):
        # 前日に1件入れて dataset が出る状態にする
        PageAnalytics.objects.create(
            page_path=f'/community/{self.community.pk}/',
            date=FIXED_TODAY - timedelta(days=1),
            content_type=PageAnalytics.ContentType.COMMUNITY,
            community=self.community, object_id=self.community.pk,
            pv=5, users=4, sessions=5,
            source_medium='flyer / qr', campaign='cmp-a',
        )
        ids = services.accessible_community_ids(self.user)
        for days in (7, 30):
            with self.subTest(days=days), _patch_today():
                result = services.get_campaign_daily_series(ids, days=days)
                labels = result['labels']
                self.assertEqual(len(labels), days)
                for ds in result['datasets']:
                    self.assertEqual(len(ds['data']), days)
                # 先頭=today-N, 末尾=today-1, 当日は含まない
                self.assertEqual(
                    labels[0],
                    (FIXED_TODAY - timedelta(days=days)).strftime('%m/%d'),
                )
                self.assertEqual(
                    labels[-1],
                    (FIXED_TODAY - timedelta(days=1)).strftime('%m/%d'),
                )
                self.assertNotIn(FIXED_TODAY.strftime('%m/%d'), labels)


class GlobalTrafficBoundaryTest(TestCase):
    """get_global_traffic（superuser 用）の当日除外を検証。"""

    def test_today_excluded_yesterday_included(self):
        PageAnalytics.objects.create(
            page_path='/', date=FIXED_TODAY,
            content_type=PageAnalytics.ContentType.GLOBAL,
            community=None, object_id=0,
            pv=100, users=80, sessions=90, source_medium='google / organic',
        )
        PageAnalytics.objects.create(
            page_path='/community/list/', date=FIXED_TODAY - timedelta(days=1),
            content_type=PageAnalytics.ContentType.GLOBAL,
            community=None, object_id=0,
            pv=200, users=180, sessions=190, source_medium='(direct) / (none)',
        )
        with _patch_today():
            result = services.get_global_traffic(days=30)
        self.assertEqual(result['total']['pv'], 200)
        paths = {r['page_path'] for r in result['top_paths']}
        self.assertIn('/community/list/', paths)
        self.assertNotIn('/', paths)


class PosterClickBoundaryTest(TestCase):
    """get_poster_click_stats の当日除外を検証。"""

    @classmethod
    def setUpTestData(cls):
        cls.community = Community.objects.create(
            name='集会P', frequency='毎週', organizers='主催',
        )

    def test_today_excluded_yesterday_included(self):
        PosterClick.objects.create(
            community=self.community, date=FIXED_TODAY, clicks=100, users=90,
        )
        PosterClick.objects.create(
            community=self.community, date=FIXED_TODAY - timedelta(days=1),
            clicks=20, users=15,
        )
        with _patch_today():
            result = services.get_poster_click_stats([self.community.pk], days=30)
        self.assertEqual(result['total']['clicks'], 20)
