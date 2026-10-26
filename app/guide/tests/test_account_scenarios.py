"""アカウント関連ガイドの撮影先を、実 view と共通部品の描画結果で照合する。"""
import os
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest import mock

from bs4 import BeautifulSoup
from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings

from guide.demo import DEMO_USERS
from user_account.models import APIKey, CustomUser


@override_settings(
    DEBUG=True,
    GUIDE_CAPTURE_EXTENDED_DEMO=True,
    SOCIALACCOUNT_PROVIDERS={'discord': {'APPS': [{'client_id': 'guide-demo', 'secret': 'DUMMY', 'key': ''}]}},
)
class AccountScenarioTargetsTest(TestCase):
    @mock.patch.dict(os.environ, {'GUIDE_DEMO_PASSWORD': 'guide-demo-test-only'})
    @mock.patch('django.utils.timezone.now', return_value=datetime(2030, 4, 1, 3, tzinfo=timezone.utc))
    def setUp(self, _now):
        demo_root = str(Path(settings.DATABASES['default']['NAME']).resolve().parent)
        with (
            override_settings(GUIDE_DEMO_ROOT=demo_root),
            mock.patch('event.notifications.post_discord_webhook'),
            self.captureOnCommitCallbacks(execute=True),
        ):
            call_command('seed_guide_demo', stdout=StringIO())
        self.user = CustomUser.objects.get(email=DEMO_USERS['owner'][1])
        self.client.force_login(self.user)

    def page(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, path)
        return BeautifulSoup(response.content, 'html.parser')

    def unique(self, soup, *selectors):
        for selector in selectors:
            with self.subTest(selector=selector):
                self.assertEqual(len(soup.select(selector)), 1, selector)

    def test_account_and_speaker_capture_targets(self):
        soup = self.page('/account/settings/')
        self.unique(soup, '#communities-heading', '#profile-heading', '#logout-heading',
                    'main form[action="/account/logout/"] button')
        self.assertEqual(soup.select_one('#communities-heading').get_text(strip=True), '管理中の集会')
        profile = soup.select_one('section[aria-labelledby="profile-heading"]')
        self.assertEqual(profile.h2.get_text(strip=True), 'プロフィールと認証')
        self.unique(profile, 'a[href="/account/update/"]', 'a[href="/account/password_change/"]',
                    'a[href="/accounts/3rdparty/"]', 'a[href="/analytics/dashboard/"]',
                    'a[href="/event/detail/history/?mine=1"]')
        for name in ('さくら技術交流会', 'こもれび技術勉強会'):
            rows = [row for row in soup.select('section[aria-labelledby="communities-heading"] .list-group-item')
                    if name in row.get_text() and row.select('input[name="community_id"]')]
            self.assertEqual(len(rows), 1)
            row = rows[0]
            forms = row.select('form')
            self.assertEqual([form.select_one('input[name="redirect_to"]')['value'] for form in forms],
                             ['/event/my_list/', '/community/settings/'])
            self.assertEqual(forms[1].button.get_text(strip=True), '設定')
        ended = [row for row in soup.select('section[aria-labelledby="communities-heading"] .list-group-item')
                 if 'ひだまり学習会' in row.get_text()]
        self.assertEqual(len(ended), 1)
        self.assertEqual(ended[0].a.get_text(strip=True), '集会を再開')

        soup = self.page('/account/update/')
        self.unique(soup, 'section[aria-labelledby="display-heading"] #id_display_name',
                    'section[aria-labelledby="display-heading"] #id_user_name',
                    'section[aria-labelledby="contact-heading"] #id_email',
                    'section[aria-labelledby="contact-heading"] #id_x_account',
                    'section[aria-labelledby="vrchat-heading"] #id_vrchat_user_id',
                    '[data-admin-action-bar] button[form="profile-form"]')
        self.assertEqual(soup.select_one('button[form="profile-form"]').get_text(strip=True), 'ユーザー情報を保存')
        for path, label in (('/account/user_name_change/', 'ユーザー名を変更'),
                            ('/account/password_change/', 'パスワードを変更')):
            soup = self.page(path)
            self.unique(soup, '#account-edit-form .admin-action-bar button[type="submit"]')
            self.assertEqual(soup.select_one('#account-edit-form button[type="submit"]').get_text(strip=True), label)

        soup = self.page('/accounts/3rdparty/')
        self.unique(soup, 'section[aria-labelledby="connections-heading"] a[href*="/disconnect/"]',
                    'nav[aria-label="パンくず"] a[href="/account/settings/"]')
        soup = self.page(soup.select_one('a[href*="/disconnect/"]')['href'])
        self.unique(soup, '#disconnect-confirm #id_password', '#disconnect-confirm button[type="submit"]',
                    '#disconnect-confirm a[href="/account/settings/"]')
        self.assertNotIn('hidden', soup.select_one('#disconnect-confirm').attrs)
        self.assertEqual(soup.select_one('#disconnect-confirm button[type="submit"]').get_text(strip=True), '解除する')
        soup = self.page('/account/discord-required/')
        self.unique(soup, 'section.admin-card a[href*="/accounts/discord/login/"]')

        self.client.force_login(CustomUser.objects.get(email=DEMO_USERS['speaker'][1]))
        soup = self.page('/account/lt-applications/')
        approved = soup.select('main article.admin-card:has(.bg-success-subtle)')
        self.assertTrue(approved)
        self.assertTrue(soup.select('main article.admin-card .bg-warning-subtle'))
        self.unique(approved[0], 'a[href$="/edit/"]')
        soup = self.page(approved[0].select_one('a[href$="/edit/"]')['href'])
        for name in ('theme', 'speaker', 'h1', 'contents', 'slide_file', 'slide_url', 'youtube_url',
                     'recording_policy', 'article_consent'):
            self.unique(soup, f'.field-wrapper[data-field="{name}"]')
        headings = [heading.get_text(strip=True) for heading in soup.select('#application-form h2')]
        self.assertEqual(headings, ['内容', '資料', '撮影・記事化'])
        self.unique(soup, '#application-form [data-admin-action-bar]', '#application-form button[type="submit"]')
        self.assertEqual(soup.select_one('#application-form button [data-submit-default]').get_text(strip=True), '保存')

    def test_api_key_creation_and_confirmation_capture_targets(self):
        soup = self.page('/account/api-keys/')
        self.unique(soup, 'form:has(#keyName)', '#keyName', 'form:has(#keyName) button[type="submit"]')
        response = self.client.post(soup.select_one('form:has(#keyName)')['action'],
                                    {'name': '記録ツール用'}, follow=True)
        self.assertEqual(response.status_code, 200)
        soup = BeautifulSoup(response.content, 'html.parser')
        self.unique(soup, 'section[aria-labelledby="new-key-heading"]', '#new-key-value',
                    '#copy-new-key', '#reveal-new-key')
        self.assertEqual(soup.select_one('#new-key-value').get_text(strip=True), '••••••••')
        self.assertEqual(soup.select_one('#copy-new-key').get_text(strip=True), 'コピー')
        self.assertEqual(soup.select_one('#reveal-new-key').get_text(strip=True), '表示')
        self.assertEqual(soup.select_one('#reveal-new-key')['aria-expanded'], 'false')
        soup = self.page('/account/api-keys/')
        self.assertIsNone(soup.select_one('#new-key-value'))
        for name in ('記録ツール用', '資料連携用'):
            rows = [row for row in soup.select('article.presentation-row') if row.h3.get_text(strip=True) == name]
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.unique(row, '[data-confirm-panel]', '.confirm-panel', '.confirm-panel button[type="submit"]',
                        '.confirm-panel [data-cancel-panel]', 'code')
            panel = row.select_one('.confirm-panel')
            self.assertEqual(row.select_one('[data-confirm-panel]')['data-confirm-panel'], panel['id'])
            self.assertIn('hidden', panel.attrs)
            self.assertEqual(row.select_one('[data-confirm-panel]').get_text(strip=True), '無効化…')
            self.assertEqual(panel.button.get_text(strip=True), 'このAPIキーを無効化する')
            if name == '記録ツール用':
                response = self.client.post(panel.form['action'], follow=True)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(APIKey.objects.get(user=self.user, name=name).is_active)
                after = BeautifulSoup(response.content, 'html.parser')
                self.assertNotIn(name, [heading.get_text(strip=True) for heading in after.select('article h3')])
