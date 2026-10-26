"""共通フォームへの移行後も、ガイドの撮影対象と操作名が描画されることを確かめる。"""
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
from user_account.models import CustomUser


@override_settings(DEBUG=True, GUIDE_CAPTURE_EXTENDED_DEMO=True)
class EventFormScenarioTargetsTest(TestCase):
    @mock.patch.dict(os.environ, {'GUIDE_DEMO_PASSWORD': 'guide-demo-test-only'})
    @mock.patch('django.utils.timezone.now', return_value=datetime(2030, 4, 1, 3, tzinfo=timezone.utc))
    def test_event_and_application_capture_targets(self, _now):
        demo_root = str(Path(settings.DATABASES['default']['NAME']).resolve().parent)
        with (
            override_settings(GUIDE_DEMO_ROOT=demo_root),
            mock.patch('event.notifications.post_discord_webhook'),
            self.captureOnCommitCallbacks(execute=True),
        ):
            call_command('seed_guide_demo', stdout=StringIO())
        self.client.force_login(CustomUser.objects.get(email=DEMO_USERS['owner'][1]))

        def page(path):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            return BeautifulSoup(response.content, 'html.parser')

        def unique(soup, *selectors):
            for selector in selectors:
                with self.subTest(selector=selector):
                    self.assertEqual(len(soup.select(selector)), 1)

        def action_bar(soup, form_id, label):
            # 保存バーはフォームの外でも、正しいフォームへ送信する必要がある。
            selector = f'[data-admin-action-bar] button[form="{form_id}"]'
            unique(soup, selector, f'#{form_id}', f'[data-admin-form-status="{form_id}"]')
            self.assertEqual(soup.select_one(selector).get_text(strip=True), label)
            self.assertIsNone(soup.select_one(f'#{form_id} button[type="submit"]'))

        soup = page('/event/calendar/create/')
        unique(soup, '#calendar-form .admin-card:has(#id_start_date)', '#id_start_time', '#id_duration',
               '#calendar-form .admin-card:has(input[name="recurrence_type"])',
               'input[name="recurrence_type"][value="weekly"]', '#id_weekday', '#weekday-field',
               'div.form-check:has(> input[value="weekly"])', 'input[value="weekly"] + label')
        headings = [h.get_text(strip=True) for h in soup.select('#calendar-form h2')]
        self.assertEqual(headings, ['開催日時', '繰り返し条件'])
        action_bar(soup, 'calendar-form', '登録')

        for path, field, label, heading in (
            ('/event/1/date/update/', 'date', '変更する', '開催日の変更'),
            ('/event/update/1/', 'start_time', '保存', '開始時刻の変更'),
        ):
            with self.subTest(path=path):
                soup = page(path)
                unique(soup, '#schedule-form', f'#id_{field}', '[data-admin-action-bar]')
                self.assertIn(heading, soup.h1.get_text())
                action_bar(soup, 'schedule-form', label)

        for path in ('/event/1/detail/create/', '/event/detail/1/update/'):
            with self.subTest(path=path):
                soup = page(path)
                unique(soup, '#detail-form .admin-card:has(#id_theme)',
                       '#detail-form .admin-card:has(#id_start_time)',
                       '#detail-form .admin-card:has(#id_detail_type_1)',
                       '#detail-form .admin-card:has(#id_detail_type_2)',
                       '#detail-settings-toggle button', '#detail-materials', '#detail-article',
                       '[aria-label="撮影"]', '.form-check:has(#id_detail_type_0)',
                       '.form-check:has(#id_detail_type_1)', '.form-check:has(#id_detail_type_2)',
                       '#detail-form button[type="submit"]')
                unique(soup, '[data-field="recording_policy"]')
                for field in ('theme', 'speaker', 'start_time', 'duration',
                              'slide_file', 'slide_url', 'thumbnail_image', 'youtube_url', 'h1', 'contents'):
                    unique(soup, f'[data-field="{field}"]', f'#id_{field}')
                content = soup.select_one('#detail-form .admin-card:has(#id_theme)')
                unique(content, '[aria-label="撮影"]')
                self.assertEqual([h.get_text(strip=True) for h in soup.select('#detail-form h2')],
                                 ['内容', '日時', '資料', '記事'])
                self.assertEqual(soup.select_one('#detail-form button[type="submit"] [data-submit-default]')
                                 .get_text(strip=True), '保存')
        unique(soup, '#article-consent-status')

        soup = page('/event/application/2/review/')
        unique(soup, '.application-information', '.application-information + .admin-card',
               '#review-form .row', '#id_event', '#id_start_time', '#id_duration',
               '#rejection-reason-field', '#id_rejection_reason', '#reject-button',
               'button[value="approve"]')
        self.assertEqual(soup.select_one('#reject-button')['form'], 'review-form')
        self.assertEqual(soup.select_one('button[value="approve"]')['form'], 'review-form')
        self.assertEqual(soup.select_one('#reject-button').get_text(strip=True), '却下する')
        self.assertEqual(soup.select_one('button[value="approve"]').get_text(strip=True), '承認する')
        self.assertIn('d-none', soup.select_one('#rejection-reason-field')['class'])
        self.assertEqual([h.get_text(strip=True) for h in soup.select('.admin-content h2')],
                         ['申請内容', '追加情報', '審査の操作'])

        soup = page('/community/calendar_update/')
        for field in ('event_detail', 'event_genres', 'join_condition', 'how_to_join', 'note',
                      'is_overseas_user', 'x_post_text'):
            unique(soup, f'.admin-form-field:has(#id_{field})', f'#id_{field}')
        unique(soup, 'label[for="id_is_overseas_user"]')
        self.assertEqual([h.get_text(strip=True) for h in soup.select('#calendar-entry-form h2')],
                         ['掲載文', '参加条件', '告知文'])
        action_bar(soup, 'calendar-entry-form', '更新')

        self.client.force_login(CustomUser.objects.get(email=DEMO_USERS['speaker'][1]))
        soup = page('/event/apply/1/')
        for field in ('event', 'theme', 'speaker', 'x_account', 'additional_info'):
            unique(soup, f'form .mb-4:has(#id_{field})', f'#id_{field}')
        unique(soup, 'form .mb-4:has([aria-label="撮影"])',
               'form .mb-4:has([aria-label="発表の記事化"])',
               '#application-form .admin-card:has(#id_additional_info)')
        self.assertEqual([h.get_text(strip=True) for h in soup.select('#application-form h2')],
                         ['集会と開催回', '発表内容', '撮影・記事化', '追加情報'])
        action_bar(soup, 'application-form', '申請する')
        soup = page('/event/apply/1/complete/')
        unique(soup, 'main h1', 'main .alert-success', 'a[href="/account/lt-applications/"]')
        self.assertIn('発表申請完了: さくら技術交流会', soup.h1.get_text())
        self.assertIn('発表を申請しました。主催者の承認をお待ちください。',
                      soup.select_one('.alert-success').get_text())
