"""検索語の日次取得・一括置換・主催者への権限付き表示を検証する。"""
import logging
from datetime import date, timedelta
from unittest.mock import call, patch

from bs4 import BeautifulSoup
from django.db import IntegrityError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError
from httplib2 import Response

from analytics.models import PageAnalytics, SearchConsoleQuery
from analytics.search_console_client import (
    SEARCH_CONSOLE_SCOPES, fetch_search_queries, sync_search_console,
)
from analytics.services import get_search_queries
from tests.factories import (
    make_community, make_discord_linked_user, make_event, make_event_detail,
)

TODAY = date(2026, 10, 23)
TARGET = TODAY - timedelta(days=1)
SECRET = 'private-test-credential'
PRIVATE_QUERY = 'private-test-search-query'


def api_row(page='https://vrc-ta-hub.com/', query='検索語', clicks=2, impressions=10, position=3.5):
    return {'keys': [page, query], 'clicks': clicks, 'impressions': impressions,
            'ctr': clicks / impressions if impressions else 0, 'position': position}


def stored_row(*, community=None, date=TARGET, query='保存済み', **extra):
    values = {
        'date': date, 'page_path': f'/community/{community.pk}/' if community else '/',
        'query': query, 'community': community,
        'content_type': PageAnalytics.ContentType.COMMUNITY if community else PageAnalytics.ContentType.GLOBAL,
        'object_id': community.pk if community else 0,
        'clicks': 2, 'impressions': 10, 'position': 3.5,
    }
    values.update(extra)
    return SearchConsoleQuery(**values)


class MockSearchConsoleMixin:
    def setUp(self):
        super().setUp()
        for name in ('build', 'compute_engine.Credentials',
                     'service_account.Credentials.from_service_account_file'):
            patcher = patch(f'analytics.search_console_client.{name}')
            mock = patcher.start()
            self.addCleanup(patcher.stop)
            if name == 'build':
                self.build = mock
            elif name == 'compute_engine.Credentials':
                self.compute_credentials = mock
            else:
                self.file_credentials = mock
        self.query = self.build.return_value.searchanalytics.return_value.query
        self.query.return_value.execute.return_value = {}


@override_settings(GOOGLE_APPLICATION_CREDENTIALS='', SEARCH_CONSOLE_SITE_URL='sc-domain:test.example')
class FetchSearchQueriesTests(MockSearchConsoleMixin, SimpleTestCase):
    def test_daily_query_and_pagination_at_25000_rows(self):
        self.query.return_value.execute.side_effect = [
            {'rows': [api_row(query=f'検索{i}') for i in range(25000)]},
            {'rows': [api_row(query='最終行')]},
        ]
        with self.assertLogs('analytics', level='INFO') as logs:
            result = fetch_search_queries(TARGET)
        self.assertEqual(len(result), 25001)
        self.assertEqual(result[-1], {
            'page_url': 'https://vrc-ta-hub.com/', 'query': '最終行',
            'clicks': 2, 'impressions': 10, 'position': 3.5,
        })
        body = {'startDate': '2026-10-22', 'endDate': '2026-10-22',
                'dimensions': ['page', 'query'], 'rowLimit': 25000, 'dataState': 'all'}
        self.assertEqual(self.query.call_args_list, [
            call(siteUrl='sc-domain:test.example', body={**body, 'startRow': 0}),
            call(siteUrl='sc-domain:test.example', body={**body, 'startRow': 25000}),
        ])
        self.compute_credentials.assert_called_once_with(scopes=SEARCH_CONSOLE_SCOPES)
        self.file_credentials.assert_not_called()
        self.build.assert_called_once_with(
            'searchconsole', 'v1', credentials=self.compute_credentials.return_value,
            cache_discovery=False,
        )
        self.assertIn('rows=25001', '\n'.join(logs.output))
        self.assertNotIn('最終行', '\n'.join(logs.output))

    def test_exact_page_size_requests_empty_final_page(self):
        self.query.return_value.execute.side_effect = [{'rows': [api_row()] * 25000}, {}]
        self.assertEqual(len(fetch_search_queries(TARGET)), 25000)
        self.assertEqual(self.query.call_count, 2)

    @override_settings(GOOGLE_APPLICATION_CREDENTIALS='/test/credentials.json')
    def test_existing_file_uses_scoped_service_account(self):
        with patch('analytics.search_console_client.os.path.exists', return_value=True):
            self.assertEqual(fetch_search_queries(TARGET), [])
        self.file_credentials.assert_called_once_with('/test/credentials.json', scopes=SEARCH_CONSOLE_SCOPES)
        self.compute_credentials.assert_not_called()
        self.assertEqual(self.build.call_args.kwargs['credentials'], self.file_credentials.return_value)

    @override_settings(GOOGLE_APPLICATION_CREDENTIALS='/test/missing.json')
    def test_missing_file_uses_metadata_credentials(self):
        with patch('analytics.search_console_client.os.path.exists', return_value=False):
            fetch_search_queries(TARGET)
        self.compute_credentials.assert_called_once_with(scopes=SEARCH_CONSOLE_SCOPES)
        self.file_credentials.assert_not_called()

    def test_errors_hide_sensitive_details_even_in_tracebacks(self):
        errors = [
            HttpError(Response({'status': '403'}), f'{SECRET} {PRIVATE_QUERY}'.encode(),
                      uri=f'https://invalid/?token={SECRET}'),
            RefreshError(f'{SECRET} {PRIVATE_QUERY}'), RuntimeError(f'{SECRET} {PRIVATE_QUERY}'),
        ]
        for error in errors:
            for phase in ('build', 'credentials', 'execute'):
                with self.subTest(error=type(error), phase=phase):
                    self.build.side_effect = error if phase == 'build' else None
                    self.compute_credentials.side_effect = error if phase == 'credentials' else None
                    self.query.return_value.execute.side_effect = error if phase == 'execute' else None
                    with self.assertLogs('analytics', level='ERROR') as logs:
                        with self.assertRaises(RuntimeError) as caught:
                            try:
                                fetch_search_queries(TARGET)
                            except RuntimeError:
                                logging.getLogger('analytics').exception('取得失敗')
                                raise
                    if isinstance(error, HttpError):
                        self.assertEqual(str(caught.exception), 'Search Console request failed: HTTP 403')
                    for value in (SECRET, PRIVATE_QUERY):
                        self.assertNotIn(value, str(caught.exception) + '\n'.join(logs.output))


@override_settings(GOOGLE_APPLICATION_CREDENTIALS='')
class SyncSearchConsoleTests(MockSearchConsoleMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.community = make_community()
        cls.event = make_event(cls.community, event_date=TARGET)
        cls.detail = make_event_detail(cls.event)

    def test_fetches_each_of_seven_days_and_resolves_full_urls(self):
        self.query.return_value.execute.return_value = {'rows': [
            api_row(f'https://vrc-ta-hub.com/community/{self.community.pk}/?ref=test#top'),
            api_row(f'https://vrc-ta-hub.com/event/detail/{self.detail.pk}/#talk'),
            api_row('https://vrc-ta-hub.com/'),
            api_row('https://vrc-ta-hub.com/community/999999/'),
            api_row('https://example.com/community/1/'),
            api_row('https://vrc-ta-hub.com.evil.example/community/1/'),
            api_row('/community/1/'),
        ]}
        with self.assertLogs('analytics', level='INFO') as logs:
            self.assertEqual(sync_search_console(TARGET), 28)
        self.assertEqual(self.query.call_count, 7)
        for ago, actual in zip(range(6, -1, -1), self.query.call_args_list):
            day = (TARGET - timedelta(days=ago)).isoformat()
            self.assertEqual(actual.kwargs['body']['startDate'], day)
            self.assertEqual(actual.kwargs['body']['endDate'], day)
            self.assertEqual(actual.kwargs['body']['startRow'], 0)
        rows = {row.page_path: row for row in SearchConsoleQuery.objects.filter(date=TARGET)}
        self.assertEqual(len(rows), 4)
        community = rows[f'/community/{self.community.pk}/']
        detail = rows[f'/event/detail/{self.detail.pk}/']
        self.assertEqual((community.community_id, community.object_id, community.content_type),
                         (self.community.pk, self.community.pk, PageAnalytics.ContentType.COMMUNITY))
        self.assertEqual((detail.community_id, detail.object_id, detail.content_type),
                         (self.community.pk, self.detail.pk, PageAnalytics.ContentType.EVENT_DETAIL))
        for path in ('/', '/community/999999/'):
            self.assertEqual(rows[path].content_type, PageAnalytics.ContentType.GLOBAL)
            self.assertIsNone(rows[path].community_id)
            self.assertEqual(rows[path].object_id, 0)
        self.assertNotIn('検索語', '\n'.join(logs.output))
        self.assertIn('saved=28 skipped=21', '\n'.join(logs.output))

    def test_refetch_replaces_each_day_and_preserves_dates_outside_window(self):
        SearchConsoleQuery.objects.bulk_create([
            stored_row(date=TARGET - timedelta(days=ago)) for ago in range(9)
        ] + [stored_row(date=TARGET + timedelta(days=1))])
        self.query.return_value.execute.return_value = {'rows': [api_row(query='新しい語', clicks=8)]}
        self.assertEqual(sync_search_console(TARGET), 7)
        self.assertFalse(SearchConsoleQuery.objects.filter(query='保存済み', date__range=(TARGET - timedelta(days=6), TARGET)).exists())
        self.assertEqual(SearchConsoleQuery.objects.filter(query='保存済み').count(), 3)
        self.assertEqual(SearchConsoleQuery.objects.get(date=TARGET).clicks, 8)
        self.query.return_value.execute.return_value = {}
        self.assertEqual(sync_search_console(TARGET), 0)
        self.assertEqual(SearchConsoleQuery.objects.count(), 3)

    def test_failure_on_middle_day_changes_nothing(self):
        SearchConsoleQuery.objects.bulk_create([stored_row(date=TARGET - timedelta(days=6))])
        before = list(SearchConsoleQuery.objects.values())
        self.query.return_value.execute.side_effect = [
            {'rows': [api_row()]}, {}, RefreshError(f'{SECRET} {PRIVATE_QUERY}'),
        ]
        with self.assertRaisesMessage(RuntimeError, 'Search Console sync failed'):
            sync_search_console(TARGET)
        self.assertEqual(list(SearchConsoleQuery.objects.values()), before)

    def test_save_failure_rolls_back_deletes_and_hides_query(self):
        SearchConsoleQuery.objects.bulk_create([stored_row(date=TARGET - timedelta(days=6))])
        before = list(SearchConsoleQuery.objects.values())
        self.query.return_value.execute.return_value = {'rows': [api_row()]}
        with patch('analytics.search_console_client.SearchConsoleQuery.objects.bulk_create',
                   side_effect=IntegrityError(f'{PRIVATE_QUERY} {SECRET}')):
            with self.assertRaisesMessage(RuntimeError, 'Search Console sync failed') as caught:
                sync_search_console(TARGET)
        self.assertNotIn(PRIVATE_QUERY, str(caught.exception))
        self.assertEqual(list(SearchConsoleQuery.objects.values()), before)

    def test_long_queries_are_truncated_and_collisions_aggregated(self):
        prefix = '語' * 255
        self.query.return_value.execute.return_value = {'rows': [
            api_row(query=prefix + 'A', clicks=1, impressions=10, position=2),
            api_row(query=prefix + 'B', clicks=2, impressions=30, position=6),
        ]}
        self.assertEqual(sync_search_console(TARGET), 7)
        row = SearchConsoleQuery.objects.get(date=TARGET)
        self.assertEqual(row.query, prefix)
        self.assertEqual((row.clicks, row.impressions, row.position), (3, 40, 5))
        self.assertIsNotNone(row.created_at)

    def test_queries_equal_under_db_collation_are_both_saved(self):
        # MySQL の照合順序で同じ値とみなされる別の検索語でも、同期が失敗せずに両方保存される。
        self.query.return_value.execute.return_value = {'rows': [
            api_row(query='ハハ', clicks=1, impressions=10, position=2),
            api_row(query='パパ', clicks=2, impressions=30, position=6),
            api_row(query='Python', clicks=0, impressions=5, position=9),
            api_row(query='python', clicks=0, impressions=7, position=8),
        ]}
        self.assertEqual(sync_search_console(TARGET), 28)
        self.assertEqual(SearchConsoleQuery.objects.filter(date=TARGET).count(), 4)

    @override_settings(REQUEST_TOKEN='test-token')
    def test_api_failure_returns_502_keeps_ga4_and_sanitizes_logs(self):
        self.query.return_value.execute.side_effect = HttpError(
            Response({'status': '403'}), f'{SECRET} {PRIVATE_QUERY}'.encode(),
            uri=f'https://invalid/?token={SECRET}',
        )
        with patch('analytics.views.fetch_page_report', return_value=[{
            'page_path': '/', 'date': TARGET, 'source_medium': '(direct) / (none)',
            'campaign': '(not set)', 'pv': 10, 'users': 8, 'sessions': 9,
        }]), patch('analytics.views.fetch_poster_click_report', return_value=[]), \
                patch('analytics.views.sync_youtube_stats', return_value=0), \
                patch('analytics.views.sync_youtube_analytics', return_value=0), \
                self.assertLogs('analytics', level='ERROR') as logs:
            response = self.client.get(reverse('analytics:sync'), {'date': TARGET.isoformat()},
                                       HTTP_REQUEST_TOKEN='test-token')
        self.assertEqual(response.status_code, 502)
        self.assertTrue(response.content.decode().endswith('YouTube: 0, YouTubeAnalytics: 0, SearchConsole: failed'))
        self.assertEqual(PageAnalytics.objects.get().pv, 10)
        self.assertFalse(SearchConsoleQuery.objects.exists())
        self.assertFalse(logs.records[0].exc_info)
        for value in (SECRET, PRIVATE_QUERY):
            self.assertNotIn(value, '\n'.join(logs.output) + response.content.decode())

    @override_settings(REQUEST_TOKEN='test-token')
    def test_success_reports_search_console_count(self):
        self.query.return_value.execute.return_value = {'rows': [api_row()]}
        with patch('analytics.views.fetch_page_report', return_value=[]), \
                patch('analytics.views.fetch_poster_click_report', return_value=[]), \
                patch('analytics.views.sync_youtube_stats', return_value=0), \
                patch('analytics.views.sync_youtube_analytics', return_value=0):
            response = self.client.get(reverse('analytics:sync'), {'date': TARGET.isoformat()},
                                       HTTP_REQUEST_TOKEN='test-token')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.decode().endswith('YouTube: 0, YouTubeAnalytics: 0, SearchConsole: 7'))

    @override_settings(REQUEST_TOKEN='test-token')
    def test_success_runs_after_youtube_failure_and_reports_saved_count(self):
        self.query.return_value.execute.return_value = {'rows': [api_row()]}
        with patch('analytics.views.fetch_page_report', return_value=[]), \
                patch('analytics.views.fetch_poster_click_report', return_value=[]), \
                patch('analytics.views.sync_youtube_stats', return_value=0), \
                patch('analytics.views.sync_youtube_analytics', side_effect=RuntimeError(SECRET)), \
                self.assertLogs('analytics', level='ERROR'):
            response = self.client.get(reverse('analytics:sync'), {'date': TARGET.isoformat()},
                                       HTTP_REQUEST_TOKEN='test-token')
        self.assertEqual(response.status_code, 502)
        self.assertTrue(response.content.decode().endswith('YouTubeAnalytics: failed, SearchConsole: 7'))
        self.assertEqual(SearchConsoleQuery.objects.count(), 7)


@patch('analytics.services.timezone.localdate', return_value=TODAY)
class SearchQueriesDisplayTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_discord_linked_user(user_name='owner', email='owner@example.com')
        cls.presenter = make_discord_linked_user(user_name='presenter', email='presenter@example.com')
        cls.community = make_community(owner=cls.owner)
        cls.other = make_community(name='他の集会')
        cls.detail = make_event_detail(make_event(cls.community, event_date=TARGET), applicant=cls.presenter)

    def test_aggregates_orders_limits_and_filters_by_permission_and_period(self, _today):
        SearchConsoleQuery.objects.bulk_create([
            stored_row(community=self.community, query=f'keyword-{i:02}', impressions=i, clicks=1)
            for i in range(12)
        ] + [
            stored_row(community=self.community, query='keyword-00', page_path=f'/event/detail/{self.detail.pk}/',
                       content_type=PageAnalytics.ContentType.EVENT_DETAIL, object_id=self.detail.pk,
                       impressions=30, clicks=7),
            stored_row(community=self.other, query='権限外の語', impressions=9999),
            stored_row(query='GLOBALの語', impressions=9999),
            stored_row(community=self.community, query='古い語', date=TODAY - timedelta(days=8), impressions=9999),
            stored_row(community=self.community, query='今日の語', date=TODAY, impressions=9999),
            stored_row(community=self.community, query='初日の語', date=TODAY - timedelta(days=7), impressions=15),
            stored_row(community=self.community, query='同数A', impressions=11, clicks=3),
            stored_row(community=self.community, query='同数B', impressions=11, clicks=3),
        ])
        rows = get_search_queries([self.community.pk], 7)
        self.assertEqual(len(rows), 10)
        self.assertEqual([row['query'] for row in rows], [
            'keyword-00', '初日の語', '同数A', '同数B', 'keyword-11',
            'keyword-10', 'keyword-09', 'keyword-08', 'keyword-07', 'keyword-06',
        ])
        self.assertEqual(rows[0], {'query': 'keyword-00', 'impressions': 30, 'clicks': 8})
        self.assertEqual(len(get_search_queries([self.community.pk], 7, limit=2)), 2)
        self.assertEqual(get_search_queries([], 7), [])
        PageAnalytics.objects.bulk_create([PageAnalytics(
            community=self.community, date=TARGET, page_path=f'/community/{self.community.pk}/',
            content_type=PageAnalytics.ContentType.COMMUNITY, object_id=self.community.pk,
            source_medium='google / organic', pv=10,
        )])
        self.client.force_login(self.owner)
        response = self.client.get(reverse('analytics:dashboard'), {'days': 7, 'community': self.other.pk})
        self.assertEqual(response.context['search_queries'], rows)
        for word in ('権限外の語', 'GLOBALの語', '古い語', '今日の語', 'keyword-01'):
            self.assertNotContains(response, word)
        self.assertContains(response, '表示 30・クリック 8')
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertEqual(len(soup.select('.analytics-search-queries > li')), 10)
        html = response.content.decode()
        self.assertLess(html.index('analytics-source-legend'), html.index('検索キーワード'))
        self.assertLess(html.index('検索キーワード'), html.index('source / medium の詳細'))

    def test_queries_equal_under_db_collation_are_aggregated_separately(self, _today):
        SearchConsoleQuery.objects.bulk_create([
            stored_row(community=self.community, query='ハハ', impressions=10, clicks=1),
            stored_row(community=self.community, query='パパ', impressions=30, clicks=2),
            stored_row(community=self.community, query='パパ', date=TARGET - timedelta(days=1), impressions=5),
        ])
        self.assertEqual(get_search_queries([self.community.pk], 7), [
            {'query': 'パパ', 'impressions': 35, 'clicks': 4},
            {'query': 'ハハ', 'impressions': 10, 'clicks': 1},
        ])

    def test_empty_message_and_help_visible_even_without_ga4(self, _today):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('analytics:dashboard'))
        self.assertContains(response, '検索キーワードはまだありません（回数の少ない語は Google が伏せます）', count=1)
        soup = BeautifulSoup(response.content, 'html.parser')
        title = next(tag for tag in soup.find_all('h3') if '検索キーワード' in tag.get_text())
        help_link = title.select_one('a[tabindex="0"][role="button"][data-bs-toggle="popover"][data-bs-trigger="focus"]')
        self.assertIsNotNone(help_link.select_one('.fa-circle-question'))
        self.assertEqual(help_link['data-bs-content'], 'Google 検索で、この集会のページが表示された時の検索語です。2〜3 日遅れて反映されます')

    def test_queries_are_escaped_and_shown_without_ga4(self, _today):
        SearchConsoleQuery.objects.bulk_create([stored_row(community=self.community, query='<script>alert(1)</script>')])
        self.client.force_login(self.owner)
        response = self.client.get(reverse('analytics:dashboard'))
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertNotContains(response, '検索キーワードはまだありません')

    def test_presenter_and_general_user_receive_no_search_context(self, _today):
        SearchConsoleQuery.objects.bulk_create([stored_row(community=self.community, query='非公開の検索語')])
        general = make_discord_linked_user(user_name='general', email='general@example.com')
        for user in (self.presenter, general):
            self.client.force_login(user)
            response = self.client.get(reverse('analytics:dashboard'))
            self.assertNotIn('search_queries', response.context)
            self.assertNotContains(response, '非公開の検索語')
            self.assertNotContains(response, '検索キーワード')

    def test_selected_accessible_community_filters_queries_and_days(self, _today):
        second = make_community(name='主催する別集会', owner=self.owner)
        SearchConsoleQuery.objects.bulk_create([
            stored_row(community=self.community, query='集会1の語'),
            stored_row(community=second, query='集会2の語', date=TODAY - timedelta(days=10)),
        ])
        self.client.force_login(self.owner)
        response = self.client.get(reverse('analytics:dashboard'), {'community': second.pk, 'days': 30})
        self.assertContains(response, '集会2の語')
        self.assertNotContains(response, '集会1の語')
        response = self.client.get(reverse('analytics:dashboard'), {'community': second.pk, 'days': 7})
        self.assertEqual(response.context['search_queries'], [])
