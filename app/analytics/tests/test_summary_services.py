"""集会合計・ページランキング・発表順位・流入元分類の集計を検証する。"""
from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from analytics import services
from analytics.models import PageAnalytics
from tests.factories import make_community, make_event, make_event_detail

TODAY = date(2026, 10, 23)


class SummaryServicesTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.community = make_community(name='集会A', frequency='毎週', organizers='主催A')
        cls.other_community = make_community(name='集会B', frequency='毎週', organizers='主催B')
        cls.event = make_event(cls.community, event_date=TODAY - timedelta(days=10))
        cls.other_event = make_event(cls.other_community, event_date=cls.event.date)
        cls.detail = make_event_detail(cls.event, theme='発表A', status='approved')
        cls.second_detail = make_event_detail(cls.event, theme='発表B', status='approved')
        cls.zero_detail = make_event_detail(cls.event, theme='閲覧なし', status='approved')
        cls.other_detail = make_event_detail(cls.other_event, theme='別集会の発表', status='approved')
        cls.pending_detail = make_event_detail(cls.event, status='pending')
        cls.rejected_detail = make_event_detail(cls.event, status='rejected')
        cls.deleted_detail = make_event_detail(cls.event, deleted_at=timezone.now(), status='approved')
        cls.blog_detail = make_event_detail(cls.event, theme='ブログ', detail_type='BLOG', status='approved')

    def setUp(self):
        patcher = patch('analytics.services.timezone.localdate', return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ids = [self.community.pk]

    def _make(self, *, kind='community', object_id=None, community=None, ago=1,
              pv=1, source='google / organic', path=None, campaign='(not set)'):
        community = community or self.community
        if object_id is None:
            object_id = community.pk if kind == 'community' else self.detail.pk
        return PageAnalytics.objects.create(
            community=community, content_type=kind, object_id=object_id,
            page_path=path or f'/{kind}/{object_id}/', date=TODAY - timedelta(days=ago),
            pv=pv, source_medium=source, campaign=campaign,
        )

    def test_summary_combines_three_types_excludes_global_and_other_communities(self):
        self._make(pv=8)
        self._make(kind='event_detail', pv=6)
        self._make(kind='campaign', pv=2)
        self._make(kind='global', pv=100)
        self._make(community=self.other_community, pv=200)
        self._make(community=self.other_community, ago=31, pv=200)
        # 前期間も同じ3種だけを合算する。
        self._make(ago=31, pv=3)
        self._make(kind='event_detail', ago=31, pv=4)
        self._make(kind='campaign', ago=31, pv=1)
        self._make(kind='global', ago=31, pv=100)
        self.assertEqual(services.get_community_summary(self.ids), {
            'pv': 16, 'prev_pv': 8, 'change_rate': 100.0,
            'community_page_pv': 8, 'article_pv': 6,
        })

    def test_summary_zero_previous_period_has_no_change_rate(self):
        self._make(pv=8)
        result = services.get_community_summary(self.ids)
        self.assertEqual(result['prev_pv'], 0)
        self.assertIsNone(result['change_rate'])

    def test_summary_negative_and_fractional_change_rate(self):
        self._make(pv=2)
        self._make(ago=31, pv=3)
        self.assertEqual(services.get_community_summary(self.ids)['change_rate'], -33.3)

    def test_summary_period_boundaries(self):
        for days in (7, 30):
            with self.subTest(days=days):
                PageAnalytics.objects.all().delete()
                for ago, pv in ((0, 1000), (1, 1), (days, 2), (days + 1, 4),
                                (days * 2, 8), (days * 2 + 1, 1000)):
                    self._make(ago=ago, pv=pv)
                result = services.get_community_summary(self.ids, days=days)
                self.assertEqual(result['pv'], 3)
                self.assertEqual(result['prev_pv'], 12)
                self.assertEqual(result['change_rate'], -75.0)

    def test_empty_ids_and_no_records(self):
        empty = {
            'pv': 0, 'prev_pv': 0, 'change_rate': None,
            'community_page_pv': 0, 'article_pv': 0,
        }
        with self.assertNumQueries(0):
            self.assertEqual(services.get_community_summary([]), empty)
            self.assertEqual(services.get_page_ranking([]), [])
            self.assertEqual(services.get_source_categories([]), [])
        self.assertEqual(services.get_community_summary(self.ids), empty)
        self.assertEqual(services.get_page_ranking(self.ids), [])
        self.assertEqual(services.get_source_categories(self.ids), [])

    def test_summary_supports_multiple_communities(self):
        self._make(pv=3)
        self._make(community=self.other_community, pv=5)
        self.assertEqual(services.get_community_summary(
            [self.community.pk, self.other_community.pk],
        )['pv'], 8)

    def test_page_ranking_metadata_aggregation_and_type_separation(self):
        self._make(pv=3)
        self._make(pv=4, source='t.co / referral')
        self._make(kind='event_detail', pv=8)
        self._make(kind='event_detail', pv=2, campaign='poster')
        self._make(kind='campaign', pv=300)
        self._make(kind='global', pv=400)
        rows = services.get_page_ranking(self.ids)
        self.assertEqual(rows, [
            {
                'kind': 'event_detail', 'object_id': self.detail.pk, 'title': '発表A',
                'url': reverse('event:detail', kwargs={'pk': self.detail.pk}),
                'published_at': self.event.date, 'community_id': self.community.pk, 'pv': 10,
            },
            {
                'kind': 'community', 'object_id': self.community.pk, 'title': '集会A',
                'url': reverse('community:detail', kwargs={'pk': self.community.pk}),
                'published_at': None, 'community_id': self.community.pk, 'pv': 7,
            },
        ])

    def test_page_ranking_ties_and_limit(self):
        self._make(kind='event_detail', object_id=self.second_detail.pk, pv=5)
        self._make(kind='event_detail', pv=5)
        rows = services.get_page_ranking(self.ids)
        self.assertEqual([row['object_id'] for row in rows], [self.detail.pk, self.second_detail.pk])
        self.assertEqual(services.get_page_ranking(self.ids, limit=1), rows[:1])
        self.assertEqual(services.get_page_ranking(self.ids, limit=0), [])

    def test_page_ranking_skips_missing_and_deleted_before_limit(self):
        self._make(kind='event_detail', object_id=999999, pv=100)
        self._make(kind='event_detail', object_id=self.deleted_detail.pk, pv=90)
        self._make(kind='community', object_id=999999, pv=80)
        self._make(kind='event_detail', pv=3)
        rows = services.get_page_ranking(self.ids, limit=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['object_id'], self.detail.pk)

    def test_page_ranking_respects_analytics_and_metadata_community_boundaries(self):
        self._make(kind='event_detail', pv=2)
        self._make(community=self.other_community, pv=100)
        self._make(kind='event_detail', community=self.other_community,
                   object_id=self.detail.pk, pv=200, path='/incorrect-other/')
        self._make(kind='event_detail', object_id=self.other_detail.pk, pv=300)
        self._make(object_id=self.other_community.pk, pv=400, path='/incorrect-community/')
        rows = services.get_page_ranking(self.ids)
        self.assertEqual([(row['object_id'], row['pv']) for row in rows], [(self.detail.pk, 2)])

    def test_page_ranking_bulk_fetches_metadata(self):
        self._make(pv=1)
        self._make(kind='event_detail', pv=2)
        self._make(kind='event_detail', object_id=self.second_detail.pk, pv=3)
        with self.assertNumQueries(3):
            self.assertEqual(len(services.get_page_ranking(self.ids)), 3)

    def test_rank_counts_public_zero_pv_lt_details_but_not_unpublished_deleted_or_blog(self):
        self._make(kind='event_detail', pv=5)
        self._make(kind='event_detail', object_id=self.second_detail.pk, pv=8)
        for detail in (self.pending_detail, self.rejected_detail, self.deleted_detail, self.blog_detail):
            self._make(kind='event_detail', object_id=detail.pk, pv=100)
        self.assertEqual(services.get_event_detail_rank([self.community.pk], self.detail), {'rank': 2, 'total': 3})
        self.assertEqual(services.get_event_detail_rank([self.community.pk], self.zero_detail), {'rank': None, 'total': 3})
        self.assertEqual(services.get_event_detail_rank([self.community.pk], self.deleted_detail), {'rank': None, 'total': 3})

    def test_rank_zero_pv_row_has_no_rank(self):
        self._make(kind='event_detail', pv=0)
        self.assertEqual(services.get_event_detail_rank([self.community.pk], self.detail), {'rank': None, 'total': 3})

    def test_rank_no_analytics_still_has_public_total(self):
        self.assertEqual(services.get_event_detail_rank([self.community.pk], self.detail), {'rank': None, 'total': 3})

    def test_rank_aggregates_sources_and_breaks_ties_by_id(self):
        self._make(kind='event_detail', pv=2)
        self._make(kind='event_detail', pv=3, source='t.co / referral')
        self._make(kind='event_detail', object_id=self.second_detail.pk, pv=5)
        self.assertEqual(services.get_event_detail_rank([self.community.pk], self.detail), {'rank': 1, 'total': 3})
        self.assertEqual(services.get_event_detail_rank([self.community.pk], self.second_detail), {'rank': 2, 'total': 3})

    def test_rank_excludes_other_community_even_with_same_object_id(self):
        self._make(kind='event_detail', pv=2)
        self._make(kind='event_detail', object_id=self.second_detail.pk, pv=3)
        self._make(kind='event_detail', community=self.other_community,
                   object_id=self.detail.pk, pv=100, path='/other-community-shared/')
        self._make(kind='event_detail', community=self.other_community,
                   object_id=self.other_detail.pk, pv=200)
        self.assertEqual(services.get_event_detail_rank([self.community.pk], self.detail), {'rank': 2, 'total': 3})

    def test_rank_returns_nothing_when_community_is_not_allowed(self):
        self._make(kind='event_detail', pv=5)
        for allowed in ([], [self.other_community.pk]):
            with self.subTest(allowed=allowed):
                self.assertEqual(services.get_event_detail_rank(allowed, self.detail), {'rank': None, 'total': 0})

    def test_categories_merge_sources_use_pv_percentages_and_sort(self):
        for source, pv in (
            ('google / organic', 2), ('yahoo / organic', 1), ('t.co / referral', 2),
            ('(direct) / (none)', 1), ('vrc-ta-hub.com / referral', 1),
            ('chatgpt.com / ai-assistant', 1), ('poster / qr', 1),
        ):
            self._make(source=source, pv=pv)
        rows = services.get_source_categories(self.ids)
        self.assertEqual([row['pv'] for row in rows], [3, 2, 1, 1, 1, 1])
        self.assertEqual(rows[0], {'category': 'search', 'label': '検索', 'pv': 3, 'percent': 33})
        by_category = {row['category']: row for row in rows}
        for category, label in (
            ('sns', 'SNS'), ('direct', '直接'), ('internal', 'Hub 内'), ('ai', 'AI'), ('other', 'その他'),
        ):
            self.assertEqual(by_category[category]['label'], label)
        self.assertEqual(by_category['sns']['percent'], 22)
        self.assertEqual(by_category['ai']['percent'], 11)

    def test_categories_object_and_content_filters_keep_community_boundary(self):
        self._make(kind='event_detail', source='t.co / referral', pv=3)
        self._make(kind='event_detail', object_id=self.second_detail.pk, pv=100)
        self._make(object_id=self.detail.pk, pv=200)
        self._make(kind='event_detail', object_id=self.detail.pk, community=self.other_community,
                   source='github.com / referral', pv=300)
        self.assertEqual(services.get_source_categories(
            self.ids, content_type='event_detail', object_id=self.detail.pk,
        ), [{'category': 'sns', 'label': 'SNS', 'pv': 3, 'percent': 100}])

    def test_categories_zero_pv_avoids_division_by_zero(self):
        self._make(pv=0)
        self.assertEqual(services.get_source_categories(self.ids), [
            {'category': 'search', 'label': '検索', 'pv': 0, 'percent': 0},
        ])

    def test_rank_ranking_and_categories_use_requested_period_through_yesterday(self):
        for ago, pv in ((0, 100), (1, 2), (7, 3), (8, 200)):
            self._make(kind='event_detail', ago=ago, pv=pv)
        self._make(kind='event_detail', object_id=self.second_detail.pk, pv=6)
        self.assertEqual(services.get_event_detail_rank([self.community.pk], self.detail, days=7), {'rank': 2, 'total': 3})
        self.assertEqual(services.get_page_ranking(self.ids, days=7)[1]['pv'], 5)
        self.assertEqual(services.get_source_categories(self.ids, days=7), [
            {'category': 'search', 'label': '検索', 'pv': 11, 'percent': 100},
        ])
