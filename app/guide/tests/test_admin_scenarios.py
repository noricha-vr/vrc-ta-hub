"""管理画面の撮影対象を、ブラウザなしで実際の描画結果と照合する。"""
import os
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest import mock

from bs4 import BeautifulSoup
from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings

from community.models import Community
from guide.demo import DEMO_USERS
from guide.demo_seeds.event import finalize
from guide.management.commands.seed_guide_demo import BASE_DATE
from twitter.models import TwitterTemplate
from user_account.models import CustomUser


@override_settings(DEBUG=True, GUIDE_CAPTURE_EXTENDED_DEMO=True)
class AdminScenarioTargetsTest(TestCase):
    @mock.patch.dict(os.environ, {'GUIDE_DEMO_PASSWORD': 'guide-demo-test-only'})
    @mock.patch('django.utils.timezone.now', return_value=datetime(2030, 4, 1, 3, tzinfo=timezone.utc))
    def test_analytics_and_twitter_capture_targets(self, _now):
        demo_root = str(Path(settings.DATABASES['default']['NAME']).resolve().parent)
        with (
            override_settings(GUIDE_DEMO_ROOT=demo_root),
            mock.patch('event.notifications.post_discord_webhook'),
            self.captureOnCommitCallbacks(execute=True),
        ):
            call_command('seed_guide_demo', stdout=StringIO())
        # TestCase では on_commit が seed の finalize より後なので、撮影時と同じ表示に戻す。
        finalize({'community': Community.objects.get(pk=1), 'base_date': BASE_DATE})
        self.client.force_login(CustomUser.objects.get(email=DEMO_USERS['owner'][1]))

        def page(path):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            return BeautifulSoup(response.content, 'html.parser')

        def unique(soup, *selectors):
            for selector in selectors:
                with self.subTest(selector=selector):
                    self.assertEqual(len(soup.select(selector)), 1)

        soup = page('/analytics/dashboard/')
        unique(soup, 'header.admin-page-header', 'nav[aria-label="集会管理"]',
               'nav[aria-label="集会管理"] a[href="/analytics/campaigns/"]',
               'form[aria-label="集会と期間の絞り込み"]', '#community-select',
               '[aria-label="集計期間"]', 'a[aria-label="CSV をダウンロード"]',
               '.card:has(.analytics-kpis)', '.analytics-kpis',
               '.card:has(#weekly-chart) h2', '.analytics-source-legend',
               '.card a[href^="/analytics/dashboard/search-queries/"]',
               '.card:has(#campaign-daily-chart) table')
        soup = page('/analytics/dashboard/search-queries/')
        unique(soup, '.card:has(#query-filter)', '#query-filter',
               'form[role="search"] button[type="submit"]', 'th a[href$="sort=impressions"]')

        for path, label in (('/analytics/campaigns/new/', '配布リンクを作成'),
                            ('/analytics/campaigns/1/edit/', '配布リンクを更新')):
            soup = page(path)
            unique(soup, 'section[aria-labelledby="campaign-target-heading"]',
                   'section[aria-labelledby="campaign-tracking-heading"]',
                   'section[aria-labelledby="campaign-schedule-heading"]',
                   '#id_community', '#id_name', '#id_landing_path', '#id_utm_source', '#id_utm_campaign',
                   '[data-admin-action-bar] button[form="campaign-form"]')
            self.assertEqual(soup.select_one('button[form="campaign-form"]').get_text(strip=True), label)
            self.assertIsNone(soup.select_one('#campaign-form button[type="submit"]'))
        soup = page('/analytics/campaigns/')
        card = soup.select_one('article.analytics-campaign:has(#campaign-1-heading)')
        self.assertIsNotNone(card)
        unique(card, '.analytics-campaign-url', '.copy-url-btn', '.analytics-campaign-heading a:has(img)',
               'a[href="/analytics/campaigns/1/edit/"]', '[data-bs-toggle="dropdown"]',
               '[data-confirm-panel="campaign-delete-1"]', '#campaign-delete-1 button[type="submit"]',
               '#campaign-delete-1 [data-cancel-panel]')
        self.assertIn('hidden', card.select_one('#campaign-delete-1').attrs)
        self.assertEqual(card.select_one('#campaign-delete-1 button[type="submit"]').get_text(strip=True),
                         '配布リンクを削除する')

        soup = page('/twitter/queue/?status=ready')
        unique(soup, 'nav[aria-label="ポストの状態で絞り込み"]')
        card = soup.select_one('article.queue-row')
        self.assertIsNotNone(card)
        unique(card, '.event-summary', '.event-actions a')
        soup = page(card.select_one('.event-actions a')['href'])
        unique(soup, 'section[aria-labelledby="queue-info-heading"]',
               'section[aria-labelledby="queue-text-heading"]', '#id_generated_text', '#id_scheduled_at')
        for field in ('#id_generated_text', '#id_scheduled_at', '#id_image_url'):
            self.assertIn('readonly', soup.select_one(field).attrs)
        self.assertIsNone(soup.select_one('#tweet-queue-form, [data-admin-action-bar], [data-confirm-panel]'))

        soup = page('/twitter/template/list/')
        edit = soup.select_one('article[data-template-delete] a')
        self.assertIsNotNone(edit)
        card = soup.select_one(f'article[data-template-delete]:has(a[href="{edit["href"]}"])')
        unique(card, '[data-confirm-panel]')
        self.assertEqual(card.parent.name, 'section')
        panel_id = card.select_one('[data-confirm-panel]')['data-confirm-panel']
        unique(card, f'#{panel_id} button[type="submit"]', f'#{panel_id} [data-cancel-panel]')
        self.assertIn('hidden', card.select_one('#' + panel_id).attrs)
        self.assertEqual(card.select_one('button[type="submit"]').get_text(strip=True), 'テンプレートを削除する')
        for path in ('/twitter/template/create/', edit['href']):
            soup = page(path)
            unique(soup, '#twitter-template-form', '#id_name', '#id_template',
                   '[data-admin-action-bar] button[form="twitter-template-form"]')
            self.assertEqual(soup.select_one('button[form="twitter-template-form"]').get_text(strip=True),
                             'テンプレートを保存')
            self.assertIsNone(soup.select_one('#twitter-template-form button[type="submit"]'))
        template = TwitterTemplate.objects.get(name='いつもの告知')
        preview_path = f'/twitter/tweet/1/{template.pk}/'
        # 生成失敗時は本文がない。シナリオが架空の結果を補う起点も照合する。
        with mock.patch('twitter.views.generate_tweet', return_value=None):
            soup = page(preview_path)
        unique(soup, 'section[aria-labelledby="tweet-heading"] .admin-empty-state',
               'section[aria-labelledby="post-heading"]', '#copy-button[disabled]')
        self.assertIsNone(soup.select_one('#tweet-text'))
        with mock.patch('twitter.views.generate_tweet', return_value='架空の告知文'):
            soup = page(preview_path)
        unique(soup, '.admin-content', '#tweet-text', '#copy-button', 'a[href="https://x.com"]')
        unique(soup, 'section[aria-labelledby="post-heading"] a[href^="https://twitter.com/intent/tweet?"]')
        self.assertNotIn('disabled', soup.select_one('#copy-button').attrs)
        self.assertEqual(soup.select_one('#copy-button').get_text(strip=True), '投稿文をコピー')
        self.assertEqual(soup.select_one('a[href="https://x.com"]').get_text(strip=True), 'X を開いて予約する ↗')
