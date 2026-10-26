"""撮影ガードとデモデータをブラウザなしで検証する。"""
import importlib.util
import os
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

    def test_command_rejects_normal_environment(self):
        with self.assertRaises(CommandError):
            call_command('seed_guide_demo', stdout=StringIO())
