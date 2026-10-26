"""撮影ガードとデモデータをブラウザなしで検証する。"""
import importlib.util
import os
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest import mock
from unittest.mock import Mock

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from bs4 import BeautifulSoup

from community.models import Community, CommunityInvitation, CommunityMember
from event.models import Event, EventDetail, RecurrenceRule
from guide.demo import DEMO_USERS
from user_account.models import CustomUser

CAPTURE_FILE = Path(__file__).resolve().parents[3] / 'scripts/guide_screenshots/capture.py'
spec = importlib.util.spec_from_file_location('guide_capture_test', CAPTURE_FILE)
assert spec is not None and spec.loader is not None
capture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture_module)


class ScreenshotGuardTest(SimpleTestCase):
    def test_demo_values_are_allowed(self):
        capture_module.assert_safe('さくら sakura@example.com https://discord.com/api/webhooks/000000000000/DUMMY')

    def test_private_values_are_rejected_without_echoing_them(self):
        cases = [
            'person@outside.test', 'person@example.com.attacker.test', '12345678901234567',
            '1234567890123456789', 'https://discord.com/api/webhooks/000000000000/REAL_TOKEN',
            'https://discord.com/api/webhooks/123456789012/DUMMY',
            'sk-' + 'a' * 32, 'ghp_' + 'b' * 36, 'AIza' + 'c' * 35,
        ]
        for value in cases:
            with self.subTest(kind=cases.index(value)):
                with self.assertRaises(ValueError) as error:
                    capture_module.assert_safe(value)
                self.assertNotIn(value, str(error.exception))


    def test_guard_stops_before_screenshot(self):
        page = Mock()
        body = Mock()
        body.inner_text.return_value = 'person@outside.test'
        fields = Mock()
        fields.evaluate_all.return_value = ''
        passwords = Mock()
        passwords.evaluate_all.return_value = False
        page.locator.side_effect = lambda selector: {
            'body': body,
            'input:not([type=hidden]), textarea, select': fields,
            'input[type=password]': passwords,
        }.get(selector, Mock())
        capture = capture_module.Capture(page, 'http://127.0.0.1:8019', 'community/create')
        with self.assertRaises(ValueError):
            capture.shot()
        page.screenshot.assert_not_called()


class GuideDemoDataTest(TestCase):
    @override_settings(DEBUG=True)
    @mock.patch.dict(os.environ, {'GUIDE_DEMO_PASSWORD': 'guide-demo-test-only'})
    def test_demo_data_and_scenario_pages(self):
        # このテストの SQLite DB だけを許可する。通常 DB の設定は変更しない。
        demo_root = str(Path(settings.DATABASES['default']['NAME']).resolve().parent)
        with override_settings(GUIDE_DEMO_ROOT=demo_root):
            call_command('seed_guide_demo', stdout=StringIO())
            counts = (CustomUser.objects.count(), Community.objects.count(), Event.objects.count(),
                      EventDetail.objects.count())
            call_command('seed_guide_demo', stdout=StringIO())
        self.assertEqual((CustomUser.objects.count(), Community.objects.count(), Event.objects.count(),
                          EventDetail.objects.count()), counts)
        # セクション別のデータ（guide/demo_seeds/）が増えても壊れないよう、共通の土台の最低限だけを見る。
        # 2 回目の実行で増えないこと（冪等）は、件数を 1 回目と比べて確かめる。
        self.assertGreaterEqual(CustomUser.objects.count(), 4)
        self.assertGreaterEqual(Community.objects.count(), 2)
        self.assertGreaterEqual(CommunityMember.objects.count(), 3)
        self.assertGreaterEqual(Event.objects.count(), 1)
        self.assertGreaterEqual(RecurrenceRule.objects.count(), 1)
        self.assertGreaterEqual(EventDetail.objects.filter(status='pending').count(), 1)
        self.assertGreaterEqual(EventDetail.objects.filter(status='approved').count(), 1)
        self.assertGreaterEqual(CommunityInvitation.objects.count(), 1)
        for role, (_, email) in DEMO_USERS.items():
            user = CustomUser.objects.get(email=email)
            self.assertTrue(user.check_password(os.environ['GUIDE_DEMO_PASSWORD']))
            self.assertTrue(user.socialaccount_set.filter(provider='discord').exists())
            self.client.force_login(user)
            if role == 'general':
                response = self.client.get('/account/settings/')
                self.assertContains(response, 'href="/community/create/"')
                response = self.client.get('/community/create/')
                self.assertContains(response, 'id="id_name"')
                self.assertContains(response, 'id="id_start_time"')
            if role == 'owner':
                response = self.client.get('/community/settings/')
                self.assertContains(response, 'さくら技術交流会')
                self.assertContains(response, 'name="notification_webhook_url"')
            if role in ('general', 'owner'):
                soup = BeautifulSoup(response.content, 'html.parser')
                for tag in soup.select('script, style'):
                    tag.decompose()
                text = soup.get_text(' ', strip=True)
                values = '\n'.join(str(tag.get('value', '')) for tag in soup.select('input:not([type=hidden])'))
                capture_module.assert_safe(text + '\n' + values)

    @override_settings(DEBUG=True, GUIDE_CAPTURE_EXTENDED_DEMO=True)
    @mock.patch.dict(os.environ, {'GUIDE_DEMO_PASSWORD': 'guide-demo-test-only'})
    @mock.patch('django.utils.timezone.now', return_value=datetime(2030, 4, 1, 3, tzinfo=timezone.utc))
    def test_mylist_capture_targets_match_demo_pages(self, _now):
        """撮影で使う開催回・メニュー・確認欄が実際の描画結果に一意に存在する。"""
        demo_root = str(Path(settings.DATABASES['default']['NAME']).resolve().parent)
        with (
            override_settings(GUIDE_DEMO_ROOT=demo_root),
            mock.patch('event.notifications.post_discord_webhook'),
            self.captureOnCommitCallbacks(execute=True),
        ):
            call_command('seed_guide_demo', stdout=StringIO())
        self.client.force_login(CustomUser.objects.get(email=DEMO_USERS['owner'][1]))
        response = self.client.get('/event/my_list/?community=1')
        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, 'html.parser')
        selectors = (
            '[aria-label="管理中の集会を切り替え"]',
            '.community-selector .dropdown-menu button.dropdown-item',
            '.mylist-header-actions a[href="/event/calendar/create/"]',
            'nav.mylist-nav a[href="/community/settings/"]',
            'nav.mylist-nav a[href^="/analytics/dashboard/"]',
            '#action-required button[data-bs-target="#approveModal2"]',
            '#action-required button[data-bs-target="#rejectModal2"]',
            'article.event-card[aria-label="4月15日の開催"] .event-date',
            '#presentation-1 .presentation-actions > a[href="/event/detail/1/update/"]',
            '#presentation-1 .presentation-actions > a[href="/event/detail/1/"]',
            '#presentation-1 button[data-bs-toggle="dropdown"]',
            '#presentation-1 .dropdown-menu [data-confirm-panel="detail-delete-1"]',
            '#presentation-2 a[href="/event/application/2/review/"]',
            '#more-future-events-toggle',
            '.vket-banner .vket-banner-actions a',
        )
        for selector in selectors:
            with self.subTest(selector=selector):
                matches = soup.select(selector)
                # 集会の切替先だけは複数ある。
                if selector.endswith('button.dropdown-item'):
                    self.assertGreater(len(matches), 1)
                else:
                    self.assertEqual(len(matches), 1)
        card = soup.select_one('article.event-card:has(a[href*="/date/update/"])')
        self.assertIsNotNone(card)
        for selector in (
            '.event-actions button[aria-label$="の開催のその他の操作"]',
            '.event-actions .dropdown-menu a[href*="/date/update/"]',
            '.event-actions .dropdown-menu a[href^="/event/update/"]',
            '.event-actions .dropdown-menu button[data-confirm-panel^="event-delete-"]',
            '.event-actions .dropdown-toggle',
            '.event-actions .dropdown-menu a[href^="/twitter/tweet/"]',
            '.event-actions > a', '.event-footer a[href="/event/1/detail/create/"]',
        ):
            with self.subTest(selector=selector):
                self.assertEqual(len(card.select(selector)), 1)
        for trigger in soup.select('[data-confirm-panel]'):
            panel = soup.select_one('#' + trigger['data-confirm-panel'])
            self.assertIsNotNone(panel)
            self.assertIn('hidden', panel.attrs)
            self.assertIsNotNone(panel.select_one('button[type="submit"]'))
            self.assertIsNotNone(panel.select_one('[data-cancel-panel]'))
        for linked in (False, True):
            detail = EventDetail.objects.get(theme='小さな実験を始める', applicant__isnull=not linked)
            row = soup.select_one(f'#presentation-{detail.pk}')
            self.assertIsNotNone(row)
            self.assertEqual(bool(row.select('a[href$="#speaker-account-heading"]')), not linked)
        # #826 の専用確認画面も、本文とキャンセルの戻り先を確かめる。
        response = self.client.get('/event/detail/1/delete/')
        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertIn('発表の削除: さくら技術交流会', soup.h1.get_text())
        for label in ('開催日:', 'テーマ: はじめてのワールド制作', '発表者: アオイ'):
            self.assertIn(label, soup.select_one('main .card').get_text())
        self.assertEqual(soup.select_one('main button[type="submit"]').get_text(strip=True), 'この発表を削除する')
        self.assertIsNotNone(soup.select_one('main form a[href="/event/detail/1/"]'))

    def test_command_rejects_normal_environment(self):
        with self.assertRaises(CommandError):
            call_command('seed_guide_demo', stdout=StringIO())
