"""ダッシュボードの動画付きランキング・週次推移・表示順を検証する。"""
import csv
import io
from datetime import date, timedelta
from unittest.mock import patch

from bs4 import BeautifulSoup
from django.test import TestCase
from django.urls import reverse

from analytics import services
from analytics.models import PageAnalytics, PosterClick, YouTubeVideoStats
from tests.factories import (
    make_community, make_community_member, make_event, make_event_detail, make_user,
)

TODAY = date(2026, 10, 23)
VIDEO_ID = 'abcdefghijk'
SECOND_VIDEO_ID = 'lmnopqrstuv'


class DashboardRankingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user()
        cls.community = make_community(name='集会A', owner=cls.user)
        cls.other_community = make_community(name='集会B')
        cls.event = make_event(cls.community, event_date=TODAY - timedelta(days=10))
        cls.detail = make_event_detail(
            cls.event, theme='発表A', status='approved', youtube_url=f'https://youtu.be/{VIDEO_ID}',
        )
        cls.second = make_event_detail(
            cls.event, theme='発表B', status='approved', youtube_url=f'https://youtu.be/{SECOND_VIDEO_ID}',
        )
        cls.no_video = make_event_detail(cls.event, theme='動画なし', status='approved', youtube_url='')
        cls.other_detail = make_event_detail(
            make_event(cls.other_community), theme='権限外の発表', status='approved',
            youtube_url='https://youtu.be/12345678901',
        )

    def setUp(self):
        patcher = patch('analytics.services.timezone.localdate', return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client.force_login(self.user)
        self.url = reverse('analytics:dashboard')
        self._analytics(pv=100)
        for detail, pv in ((self.detail, 20), (self.second, 10), (self.no_video, 5), (self.other_detail, 999)):
            self._analytics(detail=detail, pv=pv)
        self._video(self.detail, VIDEO_ID, 100, 110)
        self._video(self.second, SECOND_VIDEO_ID, 100, 130)
        self._video(self.other_detail, '12345678901', 0, 9999)

    def _analytics(self, *, detail=None, pv=1, ago=1, source='google / organic', community=None,
                   campaign='(not set)'):
        community = community or (detail.event.community if detail else self.community)
        kind = 'event_detail' if detail else 'community'
        object_id = detail.pk if detail else community.pk
        return PageAnalytics.objects.create(
            community=community, content_type=kind, object_id=object_id,
            page_path=f'/{kind}/{object_id}/', date=TODAY - timedelta(days=ago),
            pv=pv, users=pv, sessions=pv, source_medium=source, campaign=campaign,
        )

    def _video(self, detail, video_id, start, end, days=30):
        for ago, count in ((days + 1, start), (1, end)):
            YouTubeVideoStats.objects.create(
                event_detail=detail, video_id=video_id,
                date=TODAY - timedelta(days=ago), view_count=count,
            )

    def test_ranking_combines_pages_and_presentations_with_video_period(self):
        response = self.client.get(self.url)
        rows = list(response.context['page_ranking'])
        self.assertEqual([row['object_id'] for row in rows], [
            self.community.pk, self.detail.pk, self.second.pk, self.no_video.pk,
        ])
        self.assertEqual([row['kind'] for row in rows], ['community', 'event_detail', 'event_detail', 'event_detail'])
        self.assertEqual([row['video_views'] for row in rows], [None, 10, 30, None])
        self.assertEqual(rows[1]['published_at'], self.event.date)
        self.assertNotContains(response, '動画再生 ↓')
        self.assertContains(response, '>動画再生</a>', html=False)
        self.assertNotContains(response, '平均視聴時間')
        self.assertEqual(response.context['average_watch_time_by_detail'], {})
        self.assertNotContains(response, self.other_detail.theme)

    def test_summary_uses_community_summary_and_period_video_total(self):
        self._analytics(ago=31, pv=45)
        response = self.client.get(self.url)
        self.assertEqual(response.context['summary']['pv'], 135)
        self.assertEqual(response.context['summary']['change_rate'], 200.0)
        self.assertEqual(response.context['video_views_total'], 40)

    def test_summary_deduplicates_shared_videos_and_includes_videos_without_pv(self):
        make_event_detail(self.event, status='approved', youtube_url=f'https://youtube.com/watch?v={VIDEO_ID}&t=30')
        extra = make_event_detail(self.event, status='approved', youtube_url='https://youtu.be/ABCDEFGHIJK')
        self._video(extra, 'ABCDEFGHIJK', 0, 7)
        response = self.client.get(self.url)
        self.assertEqual(response.context['video_views_total'], 47)
        self.assertEqual(response.context['page_ranking'].paginator.count, 4)

    def test_summary_excludes_videos_of_unpublished_presentations(self):
        for status, video_id in (('pending', 'PENDING0001'), ('rejected', 'REJECTED001')):
            hidden = make_event_detail(self.event, status=status, youtube_url=f'https://youtu.be/{video_id}')
            self._video(hidden, video_id, 0, 500)
        response = self.client.get(self.url)
        self.assertEqual(response.context['video_views_total'], 40)

    def test_video_sort_places_available_counts_first_and_unavailable_last(self):
        response = self.client.get(self.url, {'sort': 'video'})
        self.assertEqual([row['video_views'] for row in response.context['page_ranking']], [30, 10, None, None])
        self.assertEqual(response.context['page_ranking'][0]['object_id'], self.second.pk)
        self.assertContains(response, '動画再生 ↓')

    def test_video_sort_keeps_cumulative_fallback_below_period_counts(self):
        newer = make_event_detail(self.event, theme='新しい動画', status='approved',
                                  youtube_url='https://youtu.be/ABCDEFGHIJK')
        YouTubeVideoStats.objects.create(
            event_detail=newer, video_id='ABCDEFGHIJK', date=TODAY - timedelta(days=1), view_count=10000,
        )
        self._analytics(detail=newer, pv=1)
        response = self.client.get(self.url, {'sort': 'video'})
        rows = [(row['video_views'], row['video_is_cumulative']) for row in response.context['page_ranking']]
        # 累計 10,000 は期間の 30 / 10 と比べず、その下に並べる
        self.assertEqual(rows[:3], [(30, False), (10, False), (10000, True)])
        # 期間の数字が欠けた動画があるので、合計は全体を累計にそろえる（110 + 130 + 10,000）
        self.assertEqual(response.context['video_views_total'], 10240)
        self.assertTrue(response.context['video_is_cumulative'])
        csv_response = self.client.get(self.url, {'format': 'csv', 'sort': 'video'})
        csv_rows = list(csv.DictReader(io.StringIO(csv_response.content.decode('utf-8-sig'))))
        self.assertEqual([row['動画再生数'] for row in csv_rows][:3], ['30', '10', '10000'])

    def test_invalid_sort_defaults_to_pv(self):
        response = self.client.get(self.url, {'sort': 'invalid'})
        self.assertEqual(response.context['sort'], 'pv')
        self.assertEqual(response.context['page_ranking'][0]['kind'], 'community')

    def test_missing_video_data_is_unknown_but_zero_is_displayed(self):
        YouTubeVideoStats.objects.filter(video_id=VIDEO_ID).delete()
        YouTubeVideoStats.objects.filter(video_id=SECOND_VIDEO_ID).update(view_count=100)
        response = self.client.get(self.url, {'sort': 'video'})
        self.assertEqual(response.context['video_views_total'], 0)
        self.assertEqual(response.context['page_ranking'][0]['video_views'], 0)
        self.assertEqual([row['video_views'] for row in response.context['page_ranking']], [0, None, None, None])
        self.assertContains(response, '—')

    def test_selected_community_and_days_are_preserved(self):
        make_community_member(self.other_community, self.user, role='staff')
        response = self.client.get(self.url, {'community': self.community.pk, 'days': 7, 'sort': 'video'})
        self.assertEqual(response.context['selected_community'], self.community)
        self.assertEqual(response.context['page_ranking'].paginator.count, 4)
        self.assertContains(response, f'?days=7&amp;community={self.community.pk}&sort=pv')
        self.assertContains(response, 'name="days" value="7"')
        self.assertContains(response, 'name="sort" value="video"')
        # 7日間の始点はないため、30日間の数字を流用せず累計で代わりに見せる。
        self.assertEqual(response.context['video_views_total'], 240)
        self.assertTrue(response.context['video_is_cumulative'])
        self.assertContains(response, '<span class="text-muted small ms-1">累計</span>', html=True)
        self.assertEqual(
            [(row['video_views'], row['video_is_cumulative']) for row in response.context['page_ranking']],
            [(130, True), (110, True), (None, False), (None, False)],
        )
        self.assertContains(response, '（累計）</span>')

    def test_unauthorized_community_is_ignored_for_all_analytics(self):
        response = self.client.get(self.url, {'community': self.other_community.pk})
        self.assertEqual(response.context['summary']['pv'], 135)
        self.assertEqual(response.context['video_views_total'], 40)
        self.assertEqual(sum(row['pv'] for row in response.context['weekly_series']), 135)
        self.assertNotIn('global_traffic', response.context)
        self.assertNotContains(response, self.other_detail.theme)

    def test_no_membership_keeps_analytics_out_of_context(self):
        outsider = make_user(user_name='outsider', email='outsider@example.com')
        self.client.force_login(outsider)
        with patch('analytics.dashboard_views.video_stats.get_video_views') as video_mock:
            response = self.client.get(self.url)
        video_mock.assert_not_called()
        for name in ('summary', 'page_ranking', 'weekly_series', 'video_views_total', 'source_categories',
                     'source_breakdown', 'poster_clicks', 'campaign_breakdown', 'global_traffic'):
            self.assertNotIn(name, response.context)
        self.assertContains(response, '所属している集会がありません')

    def test_csv_adds_video_column_and_keeps_missing_values_blank(self):
        response = self.client.get(self.url, {'format': 'csv', 'sort': 'video', 'community': self.other_community.pk})
        rows = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual([row['動画再生数'] for row in rows], ['30', '10', ''])
        self.assertEqual([row['動画再生数の範囲'] for row in rows], ['期間', '期間', ''])
        self.assertEqual(rows[0]['タイトル'], self.second.theme)
        self.assertNotIn(self.other_detail.theme, response.content.decode())
        self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
        self.assertNotIn('平均視聴時間', rows[0])

    def test_csv_excludes_unpublished_presentations(self):
        for status in ('pending', 'rejected'):
            hidden = make_event_detail(self.event, theme=f'非公開-{status}', status=status)
            self._analytics(detail=hidden, pv=500)
        response = self.client.get(self.url, {'format': 'csv'})
        self.assertNotIn('非公開-', response.content.decode('utf-8-sig'))
        self.assertEqual(len(list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))), 3)

    def test_csv_marks_cumulative_fallback_when_period_is_unavailable(self):
        response = self.client.get(self.url, {'format': 'csv', 'days': 7, 'sort': 'video'})
        rows = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual([row['動画再生数'] for row in rows], ['130', '110', ''])
        self.assertEqual([row['動画再生数の範囲'] for row in rows], ['累計', '累計', ''])

    def test_numbers_use_thousand_separators(self):
        PageAnalytics.objects.filter(content_type='community').update(pv=12345)
        response = self.client.get(self.url)
        self.assertContains(response, '12,380')
        self.assertContains(response, '12,345')

    def test_pagination_counts_all_rows_and_preserves_filters(self):
        for index in range(48):
            detail = make_event_detail(self.event, theme=f'発表{index}', status='approved')
            self._analytics(detail=detail)
        params = {'days': 7, 'sort': 'video', 'community': self.community.pk, 'page': 2}
        response = self.client.get(self.url, params)
        page = response.context['page_ranking']
        self.assertEqual(page.paginator.count, 52)
        self.assertEqual(page.paginator.per_page, 20)
        self.assertEqual(len(page), 20)
        self.assertContains(response, '21〜40 / 52件')
        self.assertContains(response, f'?days=7&amp;community={self.community.pk}&sort=video&page=1')
        self.assertContains(response, f'?days=7&amp;community={self.community.pk}&sort=video&page=3')
        last_page = self.client.get(self.url, {**params, 'page': 3})
        self.assertEqual(len(last_page.context['page_ranking']), 12)
        self.assertContains(last_page, '41〜52 / 52件')
        csv_response = self.client.get(self.url, {**params, 'format': 'csv'})
        rows = list(csv.DictReader(io.StringIO(csv_response.content.decode('utf-8-sig'))))
        self.assertEqual(len(rows), 51)

    def test_csv_does_not_expose_foreign_metadata_from_misassigned_analytics(self):
        self._analytics(detail=self.other_detail, community=self.community, pv=999, source='other / referral')
        response = self.client.get(self.url, {'format': 'csv'})
        self.assertNotIn(self.other_detail.theme, response.content.decode())
        self.assertNotIn(self.other_community.name, response.content.decode())

    def test_empty_state_and_section_order(self):
        PageAnalytics.objects.all().delete()
        YouTubeVideoStats.objects.all().delete()
        response = self.client.get(self.url)
        self.assertIsNone(response.context['video_views_total'])
        self.assertContains(response, '0件')
        self.assertContains(response, '対象期間にページのアクセスデータがありません。')
        self.assertContains(response, '対象期間に推移のデータがありません。')
        self.assertContains(response, '対象期間に流入元のデータがありません。')
        self.assertNotContains(response, 'ランキングのページ')
        content = response.content.decode()
        headings = ['analytics-kpis', 'ページ別ランキング', '週次推移', 'どこから',
                    '配布リンク: この期間のアクセスはありません']
        positions = [content.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))

    def test_compact_kpis_columns_and_help_text(self):
        response = self.client.get(self.url)
        soup = BeautifulSoup(response.content, 'html.parser')
        kpis = soup.select_one('.analytics-kpis')
        self.assertEqual(len(kpis.select('.analytics-kpi')), 3)
        self.assertIsNone(kpis.select_one('.badge'))  # 前期間が空なら比較を出さない
        for help_icon in kpis.select('.analytics-help'):
            self.assertEqual(help_icon['title'], help_icon['aria-label'])
            self.assertEqual(help_icon['tabindex'], '0')
            self.assertIsNotNone(help_icon.select_one('.fa-circle-question'))
        self.assertNotIn('同じ動画は1回だけ計上', kpis.get_text())
        self.assertIn('ページ別ランキング', soup.select_one('.col-lg-8').get_text())
        sidebar = soup.select_one('.col-lg-4')
        self.assertIn('週次推移', sidebar.get_text())
        self.assertIn('どこから', sidebar.get_text())
        self.assertIn('height: 180px', sidebar.select_one('#weekly-chart').parent['style'])
        self.assertEqual(len(sidebar.select('.analytics-source-legend > li')),
                         len(response.context['source_categories']))
        self.assertIn('source / medium', sidebar.select_one('details summary').get_text())

    def test_single_community_only_shows_poster_totals_in_kpi(self):
        make_community_member(self.other_community, self.user, role='staff')
        PosterClick.objects.create(community=self.community, date=TODAY - timedelta(days=1),
                                   clicks=12, users=7)
        PosterClick.objects.create(community=self.other_community, date=TODAY - timedelta(days=1),
                                   clicks=99, users=88)
        response = self.client.get(self.url, {'community': self.community.pk})
        self.assertFalse(response.context['show_poster_communities'])
        self.assertEqual(response.context['poster_clicks']['total'], {'clicks': 12, 'users': 7})
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertIsNone(soup.select_one('#poster-community-details'))
        kpi = soup.select('.analytics-kpis .analytics-kpi')[2]
        self.assertEqual(kpi.select_one('.fs-4').get_text(), '12')
        self.assertIn('7 人', kpi.get_text())

    def test_multiple_communities_show_collapsed_top_ten_poster_rows(self):
        for index in range(11):
            community = make_community(name=f'遷移集会{index}', owner=self.user)
            PosterClick.objects.create(community=community, date=TODAY - timedelta(days=1),
                                       clicks=index + 1, users=1)
        response = self.client.get(self.url)
        self.assertTrue(response.context['show_poster_communities'])
        self.assertEqual(response.context['poster_clicks']['total']['clicks'], 66)
        self.assertEqual([row['clicks'] for row in response.context['poster_community_rows']],
                         list(range(11, 1, -1)))
        details = BeautifulSoup(response.content, 'html.parser').select_one('#poster-community-details')
        self.assertIsNotNone(details)
        self.assertNotIn('open', details.attrs)
        self.assertEqual(len(details.select('tbody tr')), 10)
        self.assertNotIn('遷移集会0', details.get_text())
        self.assertNotIn(self.other_community.pk,
                         [row['community_id'] for row in response.context['poster_community_rows']])

    def test_campaign_defaults_leave_only_compact_empty_state_without_canvas(self):
        for campaign in ('(organic)', '(direct)', '(referral)', '(ai-assistant)', '(cross-network)'):
            self._analytics(campaign=campaign)
        response = self.client.get(self.url)
        self.assertEqual(response.context['campaign_breakdown'], [])
        self.assertEqual(response.context['campaign_daily']['datasets'], [])
        self.assertContains(response, '配布リンク: この期間のアクセスはありません')
        self.assertContains(response, '配布リンクを管理')
        self.assertNotContains(response, 'id="campaign-daily-chart"')
        self.assertNotContains(response, 'id="campaign-daily-data"')
        self.assertNotContains(response, 'QR コード・配布リンクからのアクセス')

    def test_real_campaign_shows_table_and_daily_chart_without_ga4_defaults(self):
        self._analytics(campaign='flyer-202610', pv=3)
        self._analytics(campaign='(organic)', pv=999)
        response = self.client.get(self.url)
        self.assertEqual([row['campaign'] for row in response.context['campaign_breakdown']], ['flyer-202610'])
        self.assertEqual([row['label'] for row in response.context['campaign_daily']['datasets']], ['flyer-202610'])
        self.assertContains(response, '<code>flyer-202610</code>', html=True)
        self.assertNotContains(response, '(organic)')
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertIn('height: 200px', soup.select_one('#campaign-daily-chart').parent['style'])

    def test_superuser_global_kpis_and_url_table_are_compact_and_collapsed(self):
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        PageAnalytics.objects.create(
            content_type='global', object_id=0, page_path='/',
            date=TODAY - timedelta(days=1), pv=1200, users=30, sessions=40,
        )
        response = self.client.get(self.url)
        soup = BeautifulSoup(response.content, 'html.parser')
        global_card = soup.select_one('#global-top-paths').find_parent(class_='card')
        self.assertEqual(len(global_card.select('.analytics-kpis .analytics-kpi')), 3)
        self.assertIn('1,200', global_card.get_text())
        self.assertNotIn('open', global_card.select_one('details').attrs)
        self.assertEqual(len(global_card.select('details tbody tr')), 1)

    def test_source_band_raw_details_and_weekly_bar_replace_daily_chart(self):
        for source in ('t.co / referral', '(direct) / (none)', 'vrc-ta-hub.com / referral',
                       'chatgpt.com / ai-assistant', 'poster / qr'):
            self._analytics(source=source)
        response = self.client.get(self.url)
        self.assertEqual(len(response.context['source_categories']), 6)
        self.assertContains(response, '<details>')
        self.assertContains(response, 'source / medium の詳細')
        self.assertContains(response, "type: 'bar'")
        self.assertNotContains(response, 'analytics-daily-chart')
        self.assertNotContains(response, 'post-publish-chart')
        self.assertContains(response, "gtag('event', 'analytics_dashboard_view'")
        self.assertContains(response, "typeof window.gtag === 'function'")


class WeeklySeriesTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.community = make_community()
        cls.other = make_community(name='権限外')

    def setUp(self):
        patcher = patch('analytics.services.timezone.localdate', return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _row(self, day, pv, *, community=None, source='google / organic'):
        community = community or self.community
        PageAnalytics.objects.create(
            community=community, content_type='community', object_id=community.pk,
            page_path=f'/community/{community.pk}/', date=day, pv=pv, source_medium=source,
        )

    def test_week_boundary_requested_period_yesterday_and_community_scope(self):
        for day, pv in ((date(2026, 10, 15), 999), (date(2026, 10, 16), 2),
                        (date(2026, 10, 18), 3), (date(2026, 10, 19), 5),
                        (date(2026, 10, 22), 7), (TODAY, 999)):
            self._row(day, pv)
        self._row(date(2026, 10, 22), 999, community=self.other)
        self._row(date(2026, 10, 22), 1, source='t.co / referral')
        self.assertEqual(services.get_weekly_series([self.community.pk], days=7), [
            {'week_start': date(2026, 10, 12), 'pv': 5},
            {'week_start': date(2026, 10, 19), 'pv': 13},
        ])

    def test_gap_weeks_are_zero_filled_in_chronological_order(self):
        self._row(date(2026, 9, 23), 2)
        self._row(date(2026, 10, 22), 3)
        rows = services.get_weekly_series([self.community.pk])
        self.assertEqual([row['pv'] for row in rows], [2, 0, 0, 0, 3])
        self.assertEqual([row['week_start'].weekday() for row in rows], [0] * 5)

    def test_empty_ids_and_no_data_return_empty_series(self):
        with self.assertNumQueries(0):
            self.assertEqual(services.get_weekly_series([]), [])
        self.assertEqual(services.get_weekly_series([self.community.pk]), [])

    def test_multiple_authorized_communities_are_summed(self):
        self._row(date(2026, 10, 22), 2)
        self._row(date(2026, 10, 22), 3, community=self.other)
        rows = services.get_weekly_series([self.community.pk, self.other.pk], days=7)
        self.assertEqual([row['pv'] for row in rows], [0, 5])
