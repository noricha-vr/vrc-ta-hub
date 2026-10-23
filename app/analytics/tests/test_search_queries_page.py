"""検索語一覧の権限・集計・絞り込みとページ間の操作を検証する。"""
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup
from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from analytics.models import PageAnalytics, SearchConsoleQuery
from analytics.services import get_all_search_queries
from analytics.tests.test_search_console import TODAY, stored_row
from tests.factories import make_community, make_discord_linked_user


@patch('analytics.services.timezone.localdate', return_value=TODAY)
class SearchQueriesPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_discord_linked_user(user_name='query-owner', email='query-owner@example.com')
        cls.community = make_community(name='検索語の集会', owner=cls.owner)
        cls.second = make_community(name='別の所属集会', owner=cls.owner)
        cls.other = make_community(name='所属していない集会')

    def setUp(self):
        self.client.force_login(self.owner)
        self.url = reverse('analytics:search_queries')

    def get_page(self, **params):
        return self.client.get(self.url, params)

    @staticmethod
    def words(response):
        return [row['query'] for row in response.context['search_queries']]

    def test_permission_period_global_and_invalid_community(self, _today):
        SearchConsoleQuery.objects.bulk_create([
            stored_row(community=self.community, query='権限内の語', clicks=8),
            stored_row(community=self.community, query='権限内の語', date=TODAY - timedelta(days=2), clicks=3),
            stored_row(community=self.second, query='別集会の語'),
            stored_row(community=self.other, query='権限外の語', clicks=999),
            stored_row(query='GLOBALの語', clicks=999),
            stored_row(community=self.community, query='紐付きGLOBALの語', content_type=PageAnalytics.ContentType.GLOBAL),
            stored_row(community=self.community, query='古い語', date=TODAY - timedelta(days=8)),
            stored_row(community=self.community, query='当日の語', date=TODAY),
            stored_row(community=self.community, query='初日の語', date=TODAY - timedelta(days=7)),
        ])
        for community in ('', self.other.pk, 'invalid', -1):
            with self.subTest(community=community):
                response = self.get_page(community=community, days=7)
                self.assertEqual(response.status_code, 200)
                self.assertCountEqual(self.words(response), ['権限内の語', '別集会の語', '初日の語'])
                self.assertEqual(response.context['search_queries'][0]['clicks'], 11)
                for word in ('権限外の語', 'GLOBALの語', '古い語', '当日の語'):
                    self.assertNotContains(response, word)
        response = self.get_page(community=self.second.pk, days=7)
        self.assertEqual(self.words(response), ['別集会の語'])
        self.assertContains(response, self.second.name)
        soup = BeautifulSoup(response.content, 'html.parser')
        back_url = reverse('analytics:dashboard') + f'?days=7&community={self.second.pk}'
        self.assertIsNotNone(soup.find('a', href=back_url))

    def test_days_allowlist_matches_dashboard(self, _today):
        SearchConsoleQuery.objects.bulk_create([
            stored_row(community=self.community, query='最近の語'),
            stored_row(community=self.community, query='20日前の語', date=TODAY - timedelta(days=20)),
            stored_row(community=self.community, query='60日前の語', date=TODAY - timedelta(days=60)),
        ])
        for days, expected in ((7, 1), (30, 2), (90, 3), ('invalid', 2), (0, 2), (365, 2)):
            with self.subTest(days=days):
                response = self.get_page(days=days)
                self.assertEqual(response.context['search_queries'].paginator.count, expected)
                dashboard = self.client.get(reverse('analytics:dashboard'), {'days': days})
                self.assertEqual(response.context['days'], dashboard.context['days'])

    def test_sorting_primary_secondary_word_order_and_aria(self, _today):
        SearchConsoleQuery.objects.bulk_create([
            stored_row(community=self.community, query=word, clicks=clicks, impressions=impressions)
            for word, clicks, impressions in (
                ('同数B', 5, 30), ('同数A', 5, 30), ('表示最多', 3, 80), ('次点', 3, 30), ('表示同数', 1, 80),
            )
        ])
        clicks_order = ['同数A', '同数B', '表示最多', '次点', '表示同数']
        for sort in ('clicks', 'impressions', 'invalid', '-clicks', ''):
            with self.subTest(sort=sort):
                response = self.get_page(sort=sort)
                active = 'impressions' if sort == 'impressions' else 'clicks'
                expected = ['表示最多', '表示同数', '同数A', '同数B', '次点'] if active == 'impressions' else clicks_order
                self.assertEqual(self.words(response), expected)
                headers = BeautifulSoup(response.content, 'html.parser').select('thead th')
                self.assertEqual([th['aria-sort'] for th in headers], [
                    'none', 'descending' if active == 'impressions' else 'none',
                    'descending' if active == 'clicks' else 'none', 'none',
                ])
                self.assertIn('↓', headers[1 if active == 'impressions' else 2].get_text())
        self.assertEqual(self.words(self.get_page()), clicks_order)

    def test_binary_grouping_and_all_results_are_not_limited_to_ten(self, _today):
        SearchConsoleQuery.objects.bulk_create([
            stored_row(community=self.community, query='ハハ', clicks=1),
            stored_row(community=self.community, query='パパ', clicks=2),
            stored_row(community=self.community, query='パパ', date=TODAY - timedelta(days=2), clicks=3),
        ] + [stored_row(community=self.community, query=f'語{i:02}') for i in range(12)])
        response = self.get_page()
        self.assertEqual(response.context['search_queries'].paginator.count, 14)
        self.assertContains(response, '14 語')
        self.assertIn('ハハ', self.words(response))
        self.assertEqual(response.context['search_queries'][0]['clicks'], 5)
        self.assertEqual(list(get_all_search_queries([], 30)), [])

    def test_filter_pagination_and_all_links_preserve_query_and_sort(self, _today):
        q = 'Python & +'
        SearchConsoleQuery.objects.bulk_create([
            stored_row(community=self.community, query=f'{q} {i:02}', impressions=100 + i)
            for i in range(51)
        ] + [stored_row(community=self.community, query='絞り込み対象外')])
        response = self.get_page(q=q, sort='impressions', days=7, community=self.community.pk)
        page = response.context['search_queries']
        self.assertEqual((page.paginator.count, page.paginator.num_pages, len(page)), (51, 2, 50))
        self.assertContains(response, '51 語')
        self.assertContains(response, '1 / 2 ページ')
        self.assertNotContains(response, '絞り込み対象外')
        soup = BeautifulSoup(response.content, 'html.parser')
        next_link = soup.find('a', string='次へ')['href']
        expected = {'q': [q], 'sort': ['impressions'], 'days': ['7'], 'community': [str(self.community.pk)], 'page': ['2']}
        self.assertEqual(parse_qs(urlsplit(next_link).query), expected)
        second = self.client.get(self.url + next_link)
        self.assertEqual(len(second.context['search_queries']), 1)
        self.assertContains(second, '2 / 2 ページ')
        soup = BeautifulSoup(second.content, 'html.parser')
        previous = parse_qs(urlsplit(soup.find('a', string='前へ')['href']).query)
        self.assertEqual(previous, {**expected, 'page': ['1']})
        for link in soup.select('thead a, [aria-label="集計期間"] a'):
            params = parse_qs(urlsplit(link['href']).query)
            self.assertEqual(params['q'], [q])
            self.assertEqual(params['community'], [str(self.community.pk)])
            self.assertNotIn('page', params)
            if link.find_parent(attrs={'aria-label': '集計期間'}):
                self.assertEqual(params['sort'], ['impressions'])
        for form in soup.select('form:has([name="q"])'):
            self.assertIsNone(form.select_one('[name="page"]'))
            self.assertEqual(form.select_one('[name="sort"]')['value'], 'impressions')
            self.assertEqual(form.select_one('[name="q"]')['value'], q)
        self.assertEqual(self.get_page(q='python').context['search_queries'].paginator.count, 51)

    def test_long_input_is_truncated_and_empty_states_are_escaped(self, _today):
        response = self.get_page()
        self.assertContains(response, 'この期間の検索キーワードはありません')
        self.assertContains(response, '0 語')
        self.assertNotContains(response, '<table')
        response = self.get_page(q='<script>')
        self.assertContains(response, '「&lt;script&gt;」を含む検索キーワードはありません')
        self.assertNotContains(response, '<table')
        SearchConsoleQuery.objects.bulk_create([stored_row(community=self.community, query='x' * 100)])
        response = self.get_page(q='x' * 150)
        self.assertEqual(response.context['q'], 'x' * 100)
        self.assertEqual(self.words(response), ['x' * 100])
        self.assertContains(response, 'maxlength="100"')

    def test_metrics_ctr_and_collection_start_note(self, _today):
        SearchConsoleQuery.objects.bulk_create([
            stored_row(community=self.community, query='通常の語', clicks=1234, impressions=10000),
            stored_row(community=self.community, query='ゼロクリック', clicks=0, impressions=10),
            stored_row(community=self.community, query='ゼロ表示', clicks=0, impressions=0),
        ])
        response = self.get_page()
        soup = BeautifulSoup(response.content, 'html.parser')
        rows = {row.th.get_text(): [cell.get_text() for cell in row.select('td')] for row in soup.select('tbody tr')}
        self.assertEqual(rows['通常の語'], ['10,000', '1,234', '12.3%'])
        self.assertEqual(rows['ゼロクリック'], ['10', '0', '0.0%'])
        self.assertEqual(rows['ゼロ表示'], ['0', '0', '—'])
        self.assertTrue(all('text-end' in cell['class'] for cell in soup.select('tbody td')))
        note = '回数の少ない語は Google が伏せるため一部だけ（2026-10-22 以降）'
        self.assertContains(response, note)
        self.assertLess(response.content.decode().index(note), response.content.decode().index('<table'))
        SearchConsoleQuery.objects.bulk_create([
            stored_row(community=self.community, query='前からある語', date=TODAY - timedelta(days=40)),
        ])
        self.assertNotContains(self.get_page(), '以降）')

    def test_anonymous_is_redirected_and_unaffiliated_user_sees_no_data(self, _today):
        SearchConsoleQuery.objects.bulk_create([stored_row(community=self.community, query='非公開の語')])
        self.client.logout()
        response = self.get_page()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response.url).path, settings.LOGIN_URL)
        self.assertEqual(parse_qs(urlsplit(response.url).query), {'next': [self.url]})
        general = make_discord_linked_user(user_name='query-general', email='query-general@example.com')
        self.client.force_login(general)
        response = self.get_page(community=self.community.pk, days=7)
        self.assertNotIn('search_queries', response.context)
        self.assertNotContains(response, '非公開の語')
        self.assertContains(response, '所属している集会がありません')
        self.assertContains(response, reverse('analytics:dashboard') + '?days=7')
