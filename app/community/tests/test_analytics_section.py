"""集会のアクセスカードの集計・表示と、context を含む権限境界を検証する。"""
from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from analytics.models import PageAnalytics, YouTubeVideoStats
from tests.factories import (
    make_community, make_community_member, make_event, make_event_detail, make_user,
)

TODAY = date(2026, 10, 23)


class CommunityDetailAnalyticsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user_a = make_user(user_name='ownerA', email='a@example.com')
        cls.user_b = make_user(user_name='ownerB', email='b@example.com')
        cls.superuser = make_user(
            user_name='admin', email='admin@example.com', is_staff=True, is_superuser=True,
        )
        cls.staff = make_user(user_name='staffA', email='staff@example.com')
        cls.community_a = make_community(name='集会A', owner=cls.user_a)
        cls.community_b = make_community(name='集会B', owner=cls.user_b)
        make_community_member(cls.community_a, cls.staff)
        cls.event = make_event(cls.community_a, event_date=TODAY - timedelta(days=10))
        cls.detail = make_event_detail(cls.event, theme='発表A', status='approved')
        for community, pv, source in (
            (cls.community_a, 100, 'google / organic'),
            (cls.community_b, 500, 'other-source-only / referral'),
        ):
            PageAnalytics.objects.create(
                page_path=reverse('community:detail', kwargs={'pk': community.pk}),
                date=TODAY - timedelta(days=1), content_type='community',
                community=community, object_id=community.pk, pv=pv, source_medium=source,
            )

    def setUp(self):
        patcher = patch('analytics.services.timezone.localdate', return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _response(self, community=None):
        return self.client.get(reverse(
            'community:detail', kwargs={'pk': (community or self.community_a).pk},
        ))

    def _card_html(self, response):
        return response.content.decode().split('id="analytics-section"', 1)[1].split('</section>', 1)[0]

    def _page(self, *, detail=None, pv=1, ago=1, source='google / organic'):
        community = detail.event.community if detail else self.community_a
        kind = 'event_detail' if detail else 'community'
        object_id = detail.pk if detail else community.pk
        return PageAnalytics.objects.create(
            community=community, content_type=kind, object_id=object_id,
            page_path=f'/{kind}/{object_id}/', date=TODAY - timedelta(days=ago),
            pv=pv, source_medium=source,
        )

    def _video(self, detail, video_id, period):
        detail.youtube_url = f'https://youtu.be/{video_id}'
        detail.save(update_fields=['youtube_url'])
        for ago, count in ((31, 100), (1, 100 + period)):
            YouTubeVideoStats.objects.create(
                event_detail=detail, video_id=video_id,
                date=TODAY - timedelta(days=ago), view_count=count,
            )

    def _assert_no_analytics(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_view_analytics'])
        for key in ('community_analytics', 'daily_series', 'source_breakdown'):
            self.assertNotIn(key, response.context)
        for text in ('id="analytics-section"', 'analytics_card_view', 'analytics_dashboard_open'):
            self.assertNotContains(response, text)

    def test_owner_sees_total_with_articles_and_previous_period(self):
        self._page(detail=self.detail, pv=350)
        self._page(ago=31, pv=200)
        self._page(detail=self.detail, ago=31, pv=100)
        self.client.force_login(self.user_a)
        response = self._response()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['community_analytics']['summary'], {
            'pv': 450, 'prev_pv': 300, 'change_rate': 50.0,
            'community_page_pv': 100, 'article_pv': 350,
        })
        self.assertContains(response, '集会のアクセス')
        card = self._card_html(response)
        self.assertIn('集会ページ 100 / 発表記事 350', card)
        self.assertIn('前の30日比 +50.0%', card)
        self.assertIn('検索 100%', card)
        self.assertNotContains(response, 'other-source-only')

    def test_ranking_filters_presentations_before_taking_top_five(self):
        details = [self.detail] + [
            make_event_detail(self.event, theme=f'発表{i}', status='approved')
            for i in range(2, 7)
        ]
        for i, detail in enumerate(details):
            self._page(detail=detail, pv=60 - i)
        self._page(detail=make_event_detail(self.event, theme='未承認', status='pending'), pv=500)
        self._page(detail=make_event_detail(
            self.event, theme='ブログ', detail_type='BLOG', status='approved',
        ), pv=500)
        self.client.force_login(self.user_a)
        response = self._response()
        rows = response.context['community_analytics']['top_presentations']
        self.assertEqual([row['object_id'] for row in rows], [d.pk for d in details[:5]])
        self.assertEqual([row['pv'] for row in rows], [60, 59, 58, 57, 56])
        card = self._card_html(response)
        self.assertEqual(card.count('<li '), 5)
        for detail in details[:5]:
            self.assertIn(reverse('event:detail', kwargs={'pk': detail.pk}), card)
        for title in ('発表6', '未承認', 'ブログ'):
            self.assertNotIn(title, card)

    def test_video_total_includes_presentations_without_page_views(self):
        self._page(detail=self.detail, pv=162)
        self._video(self.detail, 'abcdefghijk', 410)
        unranked = make_event_detail(self.event, status='approved')
        self._video(unranked, 'lmnopqrstuv', 510)
        other_event = make_event(self.community_b, event_date=TODAY)
        self._video(make_event_detail(other_event, status='approved'), '12345678901', 9000)
        self.client.force_login(self.user_a)
        response = self._response()
        card_data = response.context['community_analytics']
        self.assertEqual(card_data['video_views'], 920)
        self.assertEqual(card_data['top_presentations'][0]['video_views'], 410)
        self.assertIsNone(card_data['average_watch_time'])
        card = self._card_html(response)
        self.assertIn('920', card)
        self.assertIn('162閲覧', card)
        self.assertIn('410再生', card)
        self.assertNotIn('平均視聴時間', card)

    def test_cumulative_video_views_are_shown_until_period_is_available(self):
        self._page(detail=self.detail, pv=3)
        self.detail.youtube_url = 'https://youtu.be/abcdefghijk'
        self.detail.save(update_fields=['youtube_url'])
        YouTubeVideoStats.objects.create(
            event_detail=self.detail, video_id='abcdefghijk',
            date=TODAY - timedelta(days=1), view_count=1234,
        )
        self.client.force_login(self.user_a)
        response = self._response()
        card_data = response.context['community_analytics']
        self.assertEqual(card_data['video_views'], 1234)
        self.assertTrue(card_data['video_is_cumulative'])
        self.assertTrue(card_data['top_presentations'][0]['video_is_cumulative'])
        html = self._card_html(response)
        self.assertIn('1,234</strong><span class="small text-muted">（累計）', html)
        self.assertIn('1,234再生（累計）', html)

    def test_video_without_any_snapshot_is_not_displayed_as_zero(self):
        self._page(detail=self.detail, pv=3)
        self.detail.youtube_url = 'https://youtu.be/abcdefghijk'
        self.detail.save(update_fields=['youtube_url'])
        self.client.force_login(self.user_a)
        response = self._response()
        self.assertIsNone(response.context['community_analytics']['video_views'])
        self.assertNotIn('再生', self._card_html(response))

    def test_known_zero_video_views_are_displayed(self):
        self._page(detail=self.detail)
        self._video(self.detail, 'abcdefghijk', 0)
        self.client.force_login(self.user_a)
        response = self._response()
        self.assertEqual(response.context['community_analytics']['video_views'], 0)
        self.assertIn('動画再生', self._card_html(response))
        self.assertIn('0再生', self._card_html(response))

    def test_all_six_source_categories_use_labels_and_percentages(self):
        PageAnalytics.objects.all().delete()
        for source in ('google / organic', 't.co / referral', '(direct) / (none)',
                       'vrc-ta-hub.com / referral', 'chatgpt.com / referral', 'poster / qr'):
            self._page(detail=self.detail, source=source)
        self.client.force_login(self.user_a)
        card = self._card_html(self._response())
        for label in ('検索', 'SNS', '直接', 'Hub 内', 'AI', 'その他'):
            self.assertIn(f'{label} 17%', card)
        self.assertNotIn('google / organic', card)
        self.assertNotIn('poster / qr', card)

    def test_period_excludes_today_and_old_data(self):
        self._page(detail=self.detail, pv=3, ago=30)
        self._page(detail=self.detail, pv=1000, ago=0)
        self._page(detail=self.detail, pv=2000, ago=61)
        self.client.force_login(self.user_a)
        data = self._response().context['community_analytics']
        self.assertEqual(data['summary']['pv'], 103)
        self.assertEqual(data['top_presentations'][0]['pv'], 3)

    def test_no_previous_period_omits_comparison(self):
        self.client.force_login(self.user_a)
        response = self._response()
        self.assertIsNone(response.context['community_analytics']['summary']['change_rate'])
        self.assertNotIn('前の30日比', self._card_html(response))

    def test_zero_and_negative_change_rates_are_displayed(self):
        self.client.force_login(self.user_a)
        previous = self._page(ago=31, pv=100)
        self.assertIn('前の30日比 0.0%', self._card_html(self._response()))
        previous.pv = 200
        previous.save(update_fields=['pv'])
        self.assertIn('前の30日比 -50.0%', self._card_html(self._response()))

    def test_owner_cannot_see_others_analytics(self):
        self.client.force_login(self.user_a)
        self._assert_no_analytics(self._response(self.community_b))

    def test_anonymous_has_no_analytics(self):
        self._assert_no_analytics(self._response())

    def test_unauthorized_requests_do_not_call_analytics_services(self):
        with patch('community.views.public.analytics_services.get_community_summary') as summary, \
                patch('community.views.public.analytics_services.get_page_ranking') as ranking, \
                patch('community.views.public.analytics_services.get_source_categories') as sources, \
                patch('community.views.public.get_video_views') as videos:
            self._assert_no_analytics(self._response())
            self.client.force_login(self.user_b)
            self._assert_no_analytics(self._response())
        for service in (summary, ranking, sources, videos):
            service.assert_not_called()

    def test_superuser_sees_any_community_analytics(self):
        self.client.force_login(self.superuser)
        for community, pv in ((self.community_a, 100), (self.community_b, 500)):
            with self.subTest(community=community.pk):
                response = self._response(community)
                self.assertEqual(response.context['community_analytics']['summary']['pv'], pv)
                self.assertContains(response, 'id="analytics-section"')

    def test_analytics_appear_before_owner_contact_card(self):
        self.client.force_login(self.superuser)
        html = self._response().content.decode()
        self.assertLess(html.index('id="analytics-section"'), html.index('集会主催者の連絡先'))

    def test_staff_sees_own_community_analytics_without_edit_button(self):
        self.client.force_login(self.staff)
        response = self._response()
        self.assertTrue(response.context['can_view_analytics'])
        self.assertFalse(response.context['is_owner'])
        self.assertEqual(response.context['community_analytics']['summary']['pv'], 100)
        self.assertContains(response, 'id="analytics-section"')

    def test_staff_cannot_see_other_community_analytics(self):
        self.client.force_login(self.staff)
        self._assert_no_analytics(self._response(self.community_b))

    def test_no_data_shows_one_line_without_empty_metrics_or_link(self):
        PageAnalytics.objects.all().delete()
        self.client.force_login(self.user_a)
        response = self._response()
        self.assertFalse(response.context['community_analytics']['has_data'])
        self.assertContains(response, 'まだ集計できるアクセスがありません', count=1)
        card = self._card_html(response)
        self.assertEqual(card.count('<p '), 1)
        for text in ('詳しく見る', 'よく見られている発表', '内訳:', '閲覧', '<h2'):
            self.assertNotIn(text, card)
        self.assertContains(response, "gtag('event', 'analytics_card_view'")

    def test_video_only_data_still_shows_card(self):
        PageAnalytics.objects.all().delete()
        self._video(self.detail, 'abcdefghijk', 10)
        self.client.force_login(self.user_a)
        response = self._response()
        self.assertTrue(response.context['community_analytics']['has_data'])
        self.assertContains(response, '動画再生')
        self.assertNotContains(response, 'まだ集計できるアクセスがありません')

    def test_dashboard_link_and_guarded_ga4_events(self):
        self.client.force_login(self.user_a)
        response = self._response()
        self.assertContains(response, f'href="{reverse("analytics:dashboard")}?community={self.community_a.pk}"', count=1)
        self.assertContains(response, "gtag('event', 'analytics_card_view'", count=1)
        self.assertContains(response, "gtag('event','analytics_dashboard_open'", count=1)
        self.assertContains(response, "page_type: 'community'")
        self.assertContains(response, 'if (window.gtag)')
        self.assertContains(response, 'if(window.gtag)')

    def test_chart_context_and_scripts_are_removed(self):
        self.client.force_login(self.user_a)
        response = self._response()
        self.assertNotIn('daily_series', response.context)
        self.assertNotIn('source_breakdown', response.context)
        self.assertNotContains(response, 'id="analytics-daily-chart"')
        self.assertNotContains(response, 'cdn.jsdelivr.net/npm/chart.js')

    def test_untrusted_titles_are_escaped_and_raw_sources_are_not_rendered(self):
        unsafe = '</script><img src=x onerror=alert(1)>'
        self.detail.theme = unsafe
        self.detail.save(update_fields=['theme'])
        self._page(detail=self.detail, source=unsafe)
        self.client.force_login(self.user_a)
        response = self._response()
        self.assertNotContains(response, unsafe)
        self.assertIn('&lt;img', self._card_html(response))
